"""Workflow adapter/confirmation contracts plus real MCP discovery and HTTP paths."""
import json
import os
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import httpx
from mcp.shared.memory import create_connected_server_and_client_session
from tmcra_mcp.client import TMCRAHttpClient
from tmcra_mcp.config import MCPSettings
from tmcra_mcp.durable import DurableIngestQueue
from tmcra_mcp.server import create_server
from tmcra_mcp.workflows import WorkflowTools


class Client:
    def __init__(self): self.calls=[]
    async def _request(self,method,path,**kwargs):
        self.calls.append((method,path,kwargs))
        if method=='GET' and '/evidence?' in path:return {'items':[{'source_record_id':'s','text':'Full original selected text','role':'user'}]}
        if method=='GET':return {'available_records':[{'id':'s'}],'tasks':[{'id':'t'}],'decisions':[{'id':'d'}],'reminders':[],'bridge_plans':[{'plan_id':'plan','records':[]}],'revision':1}
        if kwargs['json_body']['action']=='bridge.preview':return {'can_commit':True,'preview_token':'token','expires_at':time.time()+300,'records':[{'item_id':'known'}]}
        return {'ok':True,'indexed':False}


class Confirmation:
    def __init__(self,accept=True):self.accept=accept;self.messages=[]
    async def elicit(self,message,schema):
        self.messages.append(message)
        return types.SimpleNamespace(action='accept' if self.accept else 'decline',data=schema(confirm=self.accept))


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client=Client();self.policy={'read':True,'write':True,'generation':1}
        self.adapter=WorkflowTools(self.client,lambda scope:scope or 'project-a',lambda s,id:self.policy.copy(),lambda p:p['write'])
    async def execute(self,action,payload=None,**kwargs):
        return await self.adapter.call(session_id='host',operation='execute',action=action,payload=payload or {},operation_id='op',**kwargs)
    async def test_read_aliases_never_post(self):
        for operation in ['snapshot','resume','timeline','reminders']:
            r=await self.adapter.call(session_id='host',operation=operation,as_of='2026-10-15T00:00:00Z')
            self.assertEqual(r['trust_boundary'],'untrusted_workflow_data')
        self.assertTrue(all(c[0]=='GET' and '?as_of=' in c[1] for c in self.client.calls))
    async def test_explicit_actions_and_no_execution_escalation(self):
        for action in ['task.upsert','decision.create','reminder.evaluate','bridge.preview']:
            await self.execute(action,{'id':'chosen'})
        self.assertEqual(len(self.client.calls),4)
        with self.assertRaises(ValueError):await self.execute('_task.executed')
        with self.assertRaises(ValueError):await self.adapter.call(session_id='host',operation='snapshot',action='task.upsert')
        with self.assertRaises(ValueError):await self.adapter.call(session_id='host',scope='../other')
    async def test_sensitive_requires_host_confirmation(self):
        for action,payload in [('record.delete',{'kind':'task','id':'t'}),('bridge.export',{'selected_ids':['s']}),('bridge.plan',{'plan_id':'plan'}),('bridge.retry_failed',{'plan_id':'plan','item_id':'s','confirmed':True})]:
            no=await self.execute(action,payload)
            self.assertEqual(no['status'],'confirmation_unavailable')
            no=await self.execute(action,payload,context=Confirmation(False))
            self.assertEqual(no['status'],'confirmation_declined')
        self.assertFalse(any(c[0]=='POST' for c in self.client.calls))
    async def test_commit_uses_observed_preview_bound_to_session(self):
        missing=await self.execute('bridge.commit',{'preview_token':'token'},context=Confirmation())
        self.assertEqual(missing['status'],'fresh_preview_required')
        await self.execute('bridge.preview',{'package':{},'selected_ids':['s']})
        other=await self.adapter.call(session_id='other',operation='execute',action='bridge.commit',payload={'preview_token':'token'},operation_id='op',context=Confirmation())
        self.assertEqual(other['status'],'fresh_preview_required')
        yes=Confirmation(); result=await self.execute('bridge.commit',{'preview_token':'token'},context=yes)
        self.assertTrue(result['data']['ok']);self.assertIn('known',yes.messages[0])
    async def test_mode_and_generation_rechecked(self):
        self.policy['write']=False
        self.assertEqual((await self.execute('task.upsert'))['status'],'session_memory_readonly')
        self.policy['write']=True
        parent=self
        class Change(Confirmation):
            async def elicit(self,*a):
                parent.policy['generation']+=1
                return await super().elicit(*a)
        self.assertEqual((await self.execute('record.delete',{'id':'t','kind':'task'},context=Change()))['status'],'context_changed')
        self.assertFalse(any(c[0]=='POST' for c in self.client.calls))
    async def test_confirmation_includes_complete_content_and_rejects_unknown_selection(self):
        yes=Confirmation()
        await self.execute('bridge.export',{'selected_ids':['s']},context=yes)
        self.assertIn('Full original selected text',yes.messages[0])
        before=len(self.client.calls)
        result=await self.execute('bridge.export',{'selected_ids':['missing']},context=yes)
        self.assertEqual(result['status'],'selection_not_found')
        self.assertEqual(len(self.client.calls),before+1)
        result=await self.execute('record.delete',{'kind':'task','id':'missing'},context=yes)
        self.assertEqual(result['status'],'selection_not_found')

    async def test_expired_server_preview_is_not_cached(self):
        original=self.client._request
        async def expired(*args,**kwargs):
            data=await original(*args,**kwargs)
            if 'expires_at' in data:data['expires_at']=time.time()-1
            return data
        self.client._request=expired
        await self.execute('bridge.preview',{'package':{},'selected_ids':['s']})
        result=await self.execute('bridge.commit',{'preview_token':'token'},context=Confirmation())
        self.assertEqual(result['status'],'fresh_preview_required')

    async def test_changed_content_after_confirmation_is_not_mutated(self):
        original=self.client._request
        changed=False
        async def request(*args,**kwargs):
            data=await original(*args,**kwargs)
            if changed and 'items' in data:
                data['items'][0]['text']='Changed while dialog was open'
            return data
        self.client._request=request
        class Change(Confirmation):
            async def elicit(self,*args):
                nonlocal changed
                changed=True
                return await super().elicit(*args)
        result=await self.execute('bridge.export',{'selected_ids':['s']},context=Change())
        self.assertEqual(result['status'],'confirmation_target_changed')
        self.assertFalse(any(c[0]=='POST' for c in self.client.calls))

    async def test_sdk_discovers_tool_and_routes_real_http_client(self):
        requests=[]
        def handler(request):
            requests.append(request)
            return httpx.Response(200,json={'available_records':[{'id':'s'}],'items':[{'source_record_id':'s','text':'SDK evidence'}],'tasks':[],'decisions':[],'reminders':[],'revision':0})
        with tempfile.TemporaryDirectory() as state, patch.dict(os.environ,{'TMCRA_MEMORY_STATE_DIR':state}):
            settings=MCPSettings('https://fixture.invalid','fixture-key','project-a',max_attempts=1)
            async with TMCRAHttpClient(settings,transport=httpx.MockTransport(handler)) as client:
                queue=DurableIngestQueue(':memory:')
                server=create_server(settings,client=client,queue=queue)
                async with create_connected_server_and_client_session(server) as session:
                    names={t.name for t in (await session.list_tools()).tools}
                    self.assertIn('tmcra_workflows',names)
                    result=await session.call_tool('tmcra_workflows',{'session_id':'host','operation':'resume'})
                    self.assertFalse(result.isError)
                    self.assertEqual(result.structuredContent['scope'],'project-a')
                    self.assertEqual(requests[-1].url.path,'/v1/scopes/project-a/workflows')
                    self.assertEqual(requests[-1].headers['authorization'],'Bearer fixture-key')
                    denied=await session.call_tool('tmcra_workflows',{'session_id':'host','operation':'execute','action':'bridge.export','payload':{'selected_ids':['s']},'operation_id':'export-once'})
                    self.assertFalse(denied.isError)
                    self.assertEqual(denied.structuredContent['status'],'confirmation_unavailable')
                    self.assertFalse(any(r.method=='POST' for r in requests))
                queue.close()
