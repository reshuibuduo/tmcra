CREATE TABLE `agents` (
	`id` text PRIMARY KEY NOT NULL,
	`organization_id` text NOT NULL,
	`name` text NOT NULL,
	`slug` text NOT NULL,
	`description` text DEFAULT '' NOT NULL,
	`status` text DEFAULT 'active' NOT NULL,
	`version` integer DEFAULT 1 NOT NULL,
	`created_by_user_id` text NOT NULL,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`archived_at` integer,
	FOREIGN KEY (`organization_id`) REFERENCES `organizations`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`created_by_user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "agents_status_check" CHECK("agents"."status" IN ('active', 'paused', 'archived'))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `agents_org_slug_uq` ON `agents` (`organization_id`,`slug`);--> statement-breakpoint
CREATE UNIQUE INDEX `agents_org_id_uq` ON `agents` (`organization_id`,`id`);--> statement-breakpoint
CREATE INDEX `agents_org_status_updated_idx` ON `agents` (`organization_id`,`status`,`updated_at`,`id`);--> statement-breakpoint
CREATE TABLE `api_keys` (
	`id` text PRIMARY KEY NOT NULL,
	`organization_id` text NOT NULL,
	`name` text NOT NULL,
	`token_prefix` text NOT NULL,
	`secret_hash` text NOT NULL,
	`hash_version` integer DEFAULT 1 NOT NULL,
	`scopes_json` text DEFAULT '[]' NOT NULL,
	`created_by_user_id` text NOT NULL,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`expires_at` integer,
	`last_used_at` integer,
	`revoked_at` integer,
	`revoked_by_user_id` text,
	FOREIGN KEY (`organization_id`) REFERENCES `organizations`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`created_by_user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`revoked_by_user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE set null,
	CONSTRAINT "api_keys_scopes_json_check" CHECK(json_valid("api_keys"."scopes_json"))
);
--> statement-breakpoint
CREATE INDEX `api_keys_org_revoked_created_idx` ON `api_keys` (`organization_id`,`revoked_at`,`created_at`,`id`);--> statement-breakpoint
CREATE TABLE `audit_logs` (
	`id` text PRIMARY KEY NOT NULL,
	`organization_id` text NOT NULL,
	`actor_type` text NOT NULL,
	`actor_user_id` text,
	`actor_api_key_id` text,
	`action` text NOT NULL,
	`target_type` text NOT NULL,
	`target_id` text NOT NULL,
	`request_id` text NOT NULL,
	`metadata_json` text DEFAULT '{}' NOT NULL,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	FOREIGN KEY (`organization_id`) REFERENCES `organizations`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`actor_user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE set null,
	FOREIGN KEY (`actor_api_key_id`) REFERENCES `api_keys`(`id`) ON UPDATE no action ON DELETE set null,
	CONSTRAINT "audit_logs_actor_type_check" CHECK("audit_logs"."actor_type" IN ('user', 'api_key', 'system')),
	CONSTRAINT "audit_logs_metadata_json_check" CHECK(json_valid("audit_logs"."metadata_json"))
);
--> statement-breakpoint
CREATE INDEX `audit_logs_org_created_idx` ON `audit_logs` (`organization_id`,`created_at`,`id`);--> statement-breakpoint
CREATE INDEX `audit_logs_org_target_created_idx` ON `audit_logs` (`organization_id`,`target_type`,`target_id`,`created_at`);--> statement-breakpoint
CREATE INDEX `audit_logs_org_actor_created_idx` ON `audit_logs` (`organization_id`,`actor_user_id`,`created_at`);--> statement-breakpoint
CREATE TABLE `memory_event_edges` (
	`id` text PRIMARY KEY NOT NULL,
	`organization_id` text NOT NULL,
	`agent_id` text NOT NULL,
	`source_event_id` text NOT NULL,
	`target_event_id` text NOT NULL,
	`relation` text NOT NULL,
	`weight` real DEFAULT 1 NOT NULL,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	FOREIGN KEY (`organization_id`,`agent_id`) REFERENCES `agents`(`organization_id`,`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`organization_id`,`agent_id`,`source_event_id`) REFERENCES `memory_events`(`organization_id`,`agent_id`,`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`organization_id`,`agent_id`,`target_event_id`) REFERENCES `memory_events`(`organization_id`,`agent_id`,`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `memory_event_edges_unique_uq` ON `memory_event_edges` (`agent_id`,`source_event_id`,`target_event_id`,`relation`);--> statement-breakpoint
CREATE INDEX `memory_event_edges_org_agent_idx` ON `memory_event_edges` (`organization_id`,`agent_id`,`created_at`,`id`);--> statement-breakpoint
CREATE TABLE `memory_events` (
	`id` text PRIMARY KEY NOT NULL,
	`organization_id` text NOT NULL,
	`agent_id` text NOT NULL,
	`event_type` text NOT NULL,
	`content_text` text NOT NULL,
	`metadata_json` text DEFAULT '{}' NOT NULL,
	`source` text DEFAULT 'console' NOT NULL,
	`idempotency_key` text,
	`occurred_at` integer NOT NULL,
	`created_by_type` text DEFAULT 'user' NOT NULL,
	`created_by_user_id` text,
	`created_by_api_key_id` text,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`redacted_at` integer,
	FOREIGN KEY (`created_by_user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE set null,
	FOREIGN KEY (`created_by_api_key_id`) REFERENCES `api_keys`(`id`) ON UPDATE no action ON DELETE set null,
	FOREIGN KEY (`organization_id`,`agent_id`) REFERENCES `agents`(`organization_id`,`id`) ON UPDATE no action ON DELETE cascade,
	CONSTRAINT "memory_events_created_by_type_check" CHECK("memory_events"."created_by_type" IN ('user', 'api_key', 'system')),
	CONSTRAINT "memory_events_metadata_json_check" CHECK(json_valid("memory_events"."metadata_json"))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `memory_events_org_agent_id_uq` ON `memory_events` (`organization_id`,`agent_id`,`id`);--> statement-breakpoint
CREATE UNIQUE INDEX `memory_events_agent_idempotency_uq` ON `memory_events` (`agent_id`,`idempotency_key`) WHERE "memory_events"."idempotency_key" IS NOT NULL;--> statement-breakpoint
CREATE INDEX `memory_events_org_agent_occurred_idx` ON `memory_events` (`organization_id`,`agent_id`,`occurred_at`,`id`);--> statement-breakpoint
CREATE INDEX `memory_events_org_created_idx` ON `memory_events` (`organization_id`,`created_at`,`id`);--> statement-breakpoint
CREATE TABLE `organization_members` (
	`organization_id` text NOT NULL,
	`user_id` text NOT NULL,
	`role` text NOT NULL,
	`status` text DEFAULT 'invited' NOT NULL,
	`invited_by_user_id` text,
	`joined_at` integer,
	`version` integer DEFAULT 1 NOT NULL,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	PRIMARY KEY(`organization_id`, `user_id`),
	FOREIGN KEY (`organization_id`) REFERENCES `organizations`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`invited_by_user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE set null,
	CONSTRAINT "organization_members_role_check" CHECK("organization_members"."role" IN ('owner', 'admin', 'developer', 'viewer')),
	CONSTRAINT "organization_members_status_check" CHECK("organization_members"."status" IN ('invited', 'active', 'suspended'))
);
--> statement-breakpoint
CREATE INDEX `organization_members_user_status_idx` ON `organization_members` (`user_id`,`status`,`organization_id`);--> statement-breakpoint
CREATE INDEX `organization_members_org_status_role_idx` ON `organization_members` (`organization_id`,`status`,`role`);--> statement-breakpoint
CREATE TABLE `organizations` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`slug` text NOT NULL,
	`status` text DEFAULT 'active' NOT NULL,
	`sample_mode` integer DEFAULT 0 NOT NULL,
	`bootstrap_owner_user_id` text,
	`created_by_user_id` text NOT NULL,
	`version` integer DEFAULT 1 NOT NULL,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	FOREIGN KEY (`bootstrap_owner_user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`created_by_user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "organizations_status_check" CHECK("organizations"."status" IN ('active', 'archived')),
	CONSTRAINT "organizations_sample_mode_check" CHECK("organizations"."sample_mode" IN (0, 1))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `organizations_slug_uq` ON `organizations` (`slug`);--> statement-breakpoint
CREATE UNIQUE INDEX `organizations_bootstrap_owner_uq` ON `organizations` (`bootstrap_owner_user_id`) WHERE "organizations"."bootstrap_owner_user_id" IS NOT NULL;--> statement-breakpoint
CREATE TABLE `schema_meta` (
	`key` text PRIMARY KEY NOT NULL,
	`value` text NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL
);
--> statement-breakpoint
CREATE TABLE `users` (
	`id` text PRIMARY KEY NOT NULL,
	`email_normalized` text NOT NULL,
	`email_display` text NOT NULL,
	`display_name` text NOT NULL,
	`bootstrap_completed_at` integer,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`last_seen_at` integer
);
--> statement-breakpoint
CREATE UNIQUE INDEX `users_email_normalized_uq` ON `users` (`email_normalized`);
--> statement-breakpoint
CREATE TRIGGER `organization_members_keep_last_owner_delete`
BEFORE DELETE ON `organization_members`
WHEN OLD.role = 'owner' AND OLD.status = 'active'
  AND NOT EXISTS (
    SELECT 1 FROM `organization_members` AS other
    WHERE other.organization_id = OLD.organization_id
      AND other.user_id <> OLD.user_id
      AND other.role = 'owner'
      AND other.status = 'active'
  )
BEGIN
  SELECT RAISE(ABORT, 'last_active_owner');
END;
--> statement-breakpoint
CREATE TRIGGER `organization_members_keep_last_owner_update`
BEFORE UPDATE OF role, status ON `organization_members`
WHEN OLD.role = 'owner' AND OLD.status = 'active'
  AND (NEW.role <> 'owner' OR NEW.status <> 'active')
  AND NOT EXISTS (
    SELECT 1 FROM `organization_members` AS other
    WHERE other.organization_id = OLD.organization_id
      AND other.user_id <> OLD.user_id
      AND other.role = 'owner'
      AND other.status = 'active'
  )
BEGIN
  SELECT RAISE(ABORT, 'last_active_owner');
END;
--> statement-breakpoint
CREATE TRIGGER `audit_logs_immutable_update`
BEFORE UPDATE ON `audit_logs`
BEGIN
  SELECT RAISE(ABORT, 'audit_log_immutable');
END;
--> statement-breakpoint
CREATE TRIGGER `audit_logs_immutable_delete`
BEFORE DELETE ON `audit_logs`
BEGIN
  SELECT RAISE(ABORT, 'audit_log_immutable');
END;
