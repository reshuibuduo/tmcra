PRAGMA foreign_keys=OFF;--> statement-breakpoint
CREATE TABLE `__new_device_connections` (
	`id` text PRIMARY KEY NOT NULL,
	`authorization_id` text NOT NULL,
	`user_id` text NOT NULL,
	`personal_space_id` text NOT NULL,
	`provider` text DEFAULT 'codex' NOT NULL,
	`display_name` text DEFAULT 'Codex' NOT NULL,
	`token_id` text NOT NULL,
	`token_prefix` text NOT NULL,
	`scope_prefix` text NOT NULL,
	`permissions_json` text DEFAULT '[]' NOT NULL,
	`status` text DEFAULT 'active' NOT NULL,
	`token_expires_at` integer NOT NULL,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`last_connected_at` integer,
	`revoked_at` integer,
	FOREIGN KEY (`authorization_id`) REFERENCES `device_authorizations`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`personal_space_id`) REFERENCES `personal_memory_spaces`(`id`) ON UPDATE no action ON DELETE cascade,
	CONSTRAINT "device_connections_provider_check" CHECK("provider" IN ('codex', 'deepseek_harness')),
	CONSTRAINT "device_connections_status_check" CHECK("status" IN ('active', 'revoked', 'expired')),
	CONSTRAINT "device_connections_permissions_json_check" CHECK(json_valid("permissions_json"))
);
--> statement-breakpoint
INSERT INTO `__new_device_connections`("id", "authorization_id", "user_id", "personal_space_id", "provider", "display_name", "token_id", "token_prefix", "scope_prefix", "permissions_json", "status", "token_expires_at", "created_at", "updated_at", "last_connected_at", "revoked_at") SELECT "id", "authorization_id", "user_id", "personal_space_id", "provider", "display_name", "token_id", "token_prefix", "scope_prefix", "permissions_json", "status", "token_expires_at", "created_at", "updated_at", "last_connected_at", "revoked_at" FROM `device_connections`;--> statement-breakpoint
DROP TABLE `device_connections`;--> statement-breakpoint
ALTER TABLE `__new_device_connections` RENAME TO `device_connections`;--> statement-breakpoint
PRAGMA foreign_keys=ON;--> statement-breakpoint
CREATE UNIQUE INDEX `device_connections_authorization_uq` ON `device_connections` (`authorization_id`);--> statement-breakpoint
CREATE UNIQUE INDEX `device_connections_token_id_uq` ON `device_connections` (`token_id`);--> statement-breakpoint
CREATE INDEX `device_connections_space_status_idx` ON `device_connections` (`personal_space_id`,`status`,`created_at`);--> statement-breakpoint
CREATE INDEX `device_connections_user_status_idx` ON `device_connections` (`user_id`,`status`,`created_at`);--> statement-breakpoint
CREATE TABLE `__new_personal_integrations` (
	`id` text PRIMARY KEY NOT NULL,
	`personal_space_id` text NOT NULL,
	`platform` text NOT NULL,
	`installation_fingerprint` text NOT NULL,
	`display_name` text NOT NULL,
	`status` text DEFAULT 'detected' NOT NULL,
	`health` text DEFAULT 'unknown' NOT NULL,
	`capabilities_json` text DEFAULT '[]' NOT NULL,
	`client_version` text,
	`integration_version` text,
	`last_error_code` text,
	`last_seen_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`last_healthy_at` integer,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`disconnected_at` integer,
	`version` integer DEFAULT 1 NOT NULL,
	FOREIGN KEY (`personal_space_id`) REFERENCES `personal_memory_spaces`(`id`) ON UPDATE no action ON DELETE cascade,
	CONSTRAINT "personal_integrations_platform_check" CHECK("platform" IN ('codex', 'openclaw', 'hermes', 'claude_code', 'deepseek_harness')),
	CONSTRAINT "personal_integrations_status_check" CHECK("status" IN ('detected', 'configured', 'connected', 'attention_required', 'disconnected')),
	CONSTRAINT "personal_integrations_health_check" CHECK("health" IN ('unknown', 'healthy', 'degraded', 'failed')),
	CONSTRAINT "personal_integrations_capabilities_json_check" CHECK(json_valid("capabilities_json")),
	CONSTRAINT "personal_integrations_version_check" CHECK("version" > 0)
);
--> statement-breakpoint
INSERT INTO `__new_personal_integrations`("id", "personal_space_id", "platform", "installation_fingerprint", "display_name", "status", "health", "capabilities_json", "client_version", "integration_version", "last_error_code", "last_seen_at", "last_healthy_at", "created_at", "updated_at", "disconnected_at", "version") SELECT "id", "personal_space_id", "platform", "installation_fingerprint", "display_name", "status", "health", "capabilities_json", "client_version", "integration_version", "last_error_code", "last_seen_at", "last_healthy_at", "created_at", "updated_at", "disconnected_at", "version" FROM `personal_integrations`;--> statement-breakpoint
DROP TABLE `personal_integrations`;--> statement-breakpoint
ALTER TABLE `__new_personal_integrations` RENAME TO `personal_integrations`;--> statement-breakpoint
CREATE UNIQUE INDEX `personal_integrations_installation_uq` ON `personal_integrations` (`personal_space_id`,`platform`,`installation_fingerprint`);--> statement-breakpoint
CREATE INDEX `personal_integrations_space_status_idx` ON `personal_integrations` (`personal_space_id`,`status`,`updated_at`);--> statement-breakpoint
ALTER TABLE `device_authorizations`
  ADD COLUMN `provider` text DEFAULT 'codex' NOT NULL
  CHECK (`provider` IN ('codex', 'deepseek_harness'));
