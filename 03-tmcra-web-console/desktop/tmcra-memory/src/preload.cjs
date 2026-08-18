"use strict";

const { contextBridge, ipcRenderer } = require("electron");

const api = Object.freeze({
  getState: () => ipcRenderer.invoke("tmcra:get-state"),
  startSetup: () => ipcRenderer.invoke("tmcra:start-setup"),
  cancelSetup: () => ipcRenderer.invoke("tmcra:cancel-setup"),
  openAuthorization: () => ipcRenderer.invoke("tmcra:open-authorization"),
  openConsole: () => ipcRenderer.invoke("tmcra:open-console"),
  acknowledgeHooks: () => ipcRenderer.invoke("tmcra:acknowledge-hooks"),
  onStateChanged: (callback) => {
    if (typeof callback !== "function") return () => {};
    const listener = (_event, state) => callback(state);
    ipcRenderer.on("tmcra:state-changed", listener);
    return () => ipcRenderer.removeListener("tmcra:state-changed", listener);
  },
});

contextBridge.exposeInMainWorld("tmcra", api);
