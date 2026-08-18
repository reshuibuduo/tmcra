CREATE TABLE `device_flow_rate_limits` (
	`limit_key` text NOT NULL,
	`bucket_start` integer NOT NULL,
	`request_count` integer DEFAULT 1 NOT NULL,
	`last_admission_id` text NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	PRIMARY KEY(`limit_key`, `bucket_start`),
	CONSTRAINT "device_flow_rate_limits_count_check" CHECK("device_flow_rate_limits"."request_count" > 0)
);
--> statement-breakpoint
CREATE INDEX `device_flow_rate_limits_bucket_idx` ON `device_flow_rate_limits` (`bucket_start`);--> statement-breakpoint
CREATE TABLE `device_revocation_outbox` (
	`id` text PRIMARY KEY NOT NULL,
	`token_id` text NOT NULL,
	`connection_id` text,
	`reason` text NOT NULL,
	`status` text DEFAULT 'pending' NOT NULL,
	`attempt_count` integer DEFAULT 0 NOT NULL,
	`next_attempt_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`last_attempt_at` integer,
	`last_error_code` text,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`completed_at` integer,
	FOREIGN KEY (`connection_id`) REFERENCES `device_connections`(`id`) ON UPDATE no action ON DELETE set null,
	CONSTRAINT "device_revocation_outbox_status_check" CHECK("device_revocation_outbox"."status" IN ('pending', 'processing', 'completed')),
	CONSTRAINT "device_revocation_outbox_attempt_check" CHECK("device_revocation_outbox"."attempt_count" >= 0)
);
--> statement-breakpoint
CREATE UNIQUE INDEX `device_revocation_outbox_token_uq` ON `device_revocation_outbox` (`token_id`);--> statement-breakpoint
CREATE INDEX `device_revocation_outbox_due_idx` ON `device_revocation_outbox` (`status`,`next_attempt_at`);--> statement-breakpoint
CREATE INDEX `device_revocation_outbox_connection_idx` ON `device_revocation_outbox` (`connection_id`);--> statement-breakpoint
ALTER TABLE `device_authorizations` ADD `source_hash` text DEFAULT 'legacy' NOT NULL;--> statement-breakpoint
ALTER TABLE `device_authorizations` ADD `issuance_request_id` text;