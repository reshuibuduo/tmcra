CREATE TABLE `internal_action_limits` (
	`actor_staff_id` text NOT NULL,
	`bucket_start` integer NOT NULL,
	`mutation_count` integer DEFAULT 1 NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	PRIMARY KEY(`actor_staff_id`, `bucket_start`),
	CONSTRAINT "internal_action_limits_count_check" CHECK("internal_action_limits"."mutation_count" > 0)
);
--> statement-breakpoint
CREATE INDEX `internal_action_limits_bucket_idx` ON `internal_action_limits` (`bucket_start`);--> statement-breakpoint
CREATE TABLE `internal_audit_logs` (
	`id` text PRIMARY KEY NOT NULL,
	`actor_staff_id` text,
	`actor_email` text NOT NULL,
	`actor_role` text NOT NULL,
	`action` text NOT NULL,
	`target_type` text NOT NULL,
	`target_id` text NOT NULL,
	`request_id` text NOT NULL,
	`metadata_json` text DEFAULT '{}' NOT NULL,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	CONSTRAINT "internal_audit_logs_actor_role_check" CHECK("internal_audit_logs"."actor_role" IN ('platform_owner', 'platform_admin', 'support', 'security', 'analyst')),
	CONSTRAINT "internal_audit_logs_metadata_json_check" CHECK(json_valid("internal_audit_logs"."metadata_json"))
);
--> statement-breakpoint
CREATE INDEX `internal_audit_logs_created_idx` ON `internal_audit_logs` (`created_at`,`id`);--> statement-breakpoint
CREATE INDEX `internal_audit_logs_actor_created_idx` ON `internal_audit_logs` (`actor_staff_id`,`created_at`);--> statement-breakpoint
CREATE INDEX `internal_audit_logs_target_created_idx` ON `internal_audit_logs` (`target_type`,`target_id`,`created_at`);--> statement-breakpoint
CREATE TABLE `internal_meta` (
	`key` text PRIMARY KEY NOT NULL,
	`value` text NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL
);
--> statement-breakpoint
CREATE TABLE `internal_staff` (
	`id` text PRIMARY KEY NOT NULL,
	`email_normalized` text NOT NULL,
	`email_display` text NOT NULL,
	`display_name` text NOT NULL,
	`role` text NOT NULL,
	`status` text DEFAULT 'invited' NOT NULL,
	`invited_by_staff_id` text,
	`joined_at` integer,
	`last_seen_at` integer,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	CONSTRAINT "internal_staff_role_check" CHECK("internal_staff"."role" IN ('platform_owner', 'platform_admin', 'support', 'security', 'analyst')),
	CONSTRAINT "internal_staff_status_check" CHECK("internal_staff"."status" IN ('invited', 'active', 'suspended'))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `internal_staff_email_normalized_uq` ON `internal_staff` (`email_normalized`);--> statement-breakpoint
CREATE INDEX `internal_staff_status_role_idx` ON `internal_staff` (`status`,`role`,`created_at`);--> statement-breakpoint
CREATE TRIGGER `internal_staff_keep_last_owner_delete`
BEFORE DELETE ON `internal_staff`
WHEN OLD.role = 'platform_owner' AND OLD.status = 'active'
  AND NOT EXISTS (
    SELECT 1 FROM `internal_staff` AS other
    WHERE other.id <> OLD.id
      AND other.role = 'platform_owner'
      AND other.status = 'active'
  )
BEGIN
  SELECT RAISE(ABORT, 'internal_last_platform_owner');
END;--> statement-breakpoint
CREATE TRIGGER `internal_staff_keep_last_owner_update`
BEFORE UPDATE OF role, status ON `internal_staff`
WHEN OLD.role = 'platform_owner' AND OLD.status = 'active'
  AND (NEW.role <> 'platform_owner' OR NEW.status <> 'active')
  AND NOT EXISTS (
    SELECT 1 FROM `internal_staff` AS other
    WHERE other.id <> OLD.id
      AND other.role = 'platform_owner'
      AND other.status = 'active'
  )
BEGIN
  SELECT RAISE(ABORT, 'internal_last_platform_owner');
END;--> statement-breakpoint
CREATE TRIGGER `internal_audit_logs_immutable_update`
BEFORE UPDATE ON `internal_audit_logs`
BEGIN
  SELECT RAISE(ABORT, 'internal_audit_immutable');
END;--> statement-breakpoint
CREATE TRIGGER `internal_audit_logs_immutable_delete`
BEFORE DELETE ON `internal_audit_logs`
BEGIN
  SELECT RAISE(ABORT, 'internal_audit_immutable');
END;--> statement-breakpoint
CREATE TRIGGER `internal_bootstrap_meta_immutable_update`
BEFORE UPDATE ON `internal_meta`
WHEN OLD.key = 'internal_bootstrap_owner_email'
BEGIN
  SELECT RAISE(ABORT, 'internal_bootstrap_locked');
END;--> statement-breakpoint
CREATE TRIGGER `internal_bootstrap_meta_immutable_delete`
BEFORE DELETE ON `internal_meta`
WHEN OLD.key = 'internal_bootstrap_owner_email'
BEGIN
  SELECT RAISE(ABORT, 'internal_bootstrap_locked');
END;
