CREATE TABLE `personal_integrations` (
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
	CONSTRAINT "personal_integrations_platform_check" CHECK("personal_integrations"."platform" IN ('codex', 'openclaw', 'hermes', 'claude_code')),
	CONSTRAINT "personal_integrations_status_check" CHECK("personal_integrations"."status" IN ('detected', 'configured', 'connected', 'attention_required', 'disconnected')),
	CONSTRAINT "personal_integrations_health_check" CHECK("personal_integrations"."health" IN ('unknown', 'healthy', 'degraded', 'failed')),
	CONSTRAINT "personal_integrations_capabilities_json_check" CHECK(json_valid("personal_integrations"."capabilities_json")),
	CONSTRAINT "personal_integrations_version_check" CHECK("personal_integrations"."version" > 0)
);
--> statement-breakpoint
CREATE UNIQUE INDEX `personal_integrations_installation_uq` ON `personal_integrations` (`personal_space_id`,`platform`,`installation_fingerprint`);--> statement-breakpoint
CREATE INDEX `personal_integrations_space_status_idx` ON `personal_integrations` (`personal_space_id`,`status`,`updated_at`);
