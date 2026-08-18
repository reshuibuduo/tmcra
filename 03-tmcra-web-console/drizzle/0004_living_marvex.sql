CREATE TABLE `account_profiles` (
	`user_id` text PRIMARY KEY NOT NULL,
	`account_type` text,
	`status` text DEFAULT 'active' NOT NULL,
	`selected_at` integer,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade,
	CONSTRAINT "account_profiles_type_check" CHECK("account_profiles"."account_type" IS NULL OR "account_profiles"."account_type" IN ('personal', 'enterprise')),
	CONSTRAINT "account_profiles_status_check" CHECK("account_profiles"."status" IN ('active', 'suspended'))
);
--> statement-breakpoint
CREATE INDEX `account_profiles_type_status_idx` ON `account_profiles` (`account_type`,`status`,`updated_at`);--> statement-breakpoint
CREATE TABLE `personal_memory_spaces` (
	`id` text PRIMARY KEY NOT NULL,
	`user_id` text NOT NULL,
	`scope_name` text NOT NULL,
	`display_name` text NOT NULL,
	`status` text DEFAULT 'active' NOT NULL,
	`version` integer DEFAULT 1 NOT NULL,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade,
	CONSTRAINT "personal_memory_spaces_status_check" CHECK("personal_memory_spaces"."status" IN ('active', 'deleting', 'deleted'))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `personal_memory_spaces_user_uq` ON `personal_memory_spaces` (`user_id`);--> statement-breakpoint
CREATE UNIQUE INDEX `personal_memory_spaces_scope_uq` ON `personal_memory_spaces` (`scope_name`);--> statement-breakpoint
CREATE INDEX `personal_memory_spaces_status_updated_idx` ON `personal_memory_spaces` (`status`,`updated_at`);
--> statement-breakpoint
INSERT OR IGNORE INTO `account_profiles` (
	`user_id`, `account_type`, `status`, `selected_at`, `created_at`, `updated_at`
)
SELECT
	u.`id`,
	CASE WHEN EXISTS (
		SELECT 1 FROM `organization_members` m WHERE m.`user_id` = u.`id`
	) THEN 'enterprise' ELSE NULL END,
	'active',
	CASE WHEN EXISTS (
		SELECT 1 FROM `organization_members` m WHERE m.`user_id` = u.`id`
	) THEN (unixepoch() * 1000) ELSE NULL END,
	(unixepoch() * 1000),
	(unixepoch() * 1000)
FROM `users` u;
--> statement-breakpoint
CREATE TRIGGER `account_profiles_personal_no_active_enterprise`
BEFORE UPDATE OF `account_type` ON `account_profiles`
WHEN NEW.`account_type` = 'personal'
  AND EXISTS (
	SELECT 1 FROM `organization_members`
	WHERE `user_id` = NEW.`user_id` AND `status` = 'active'
  )
BEGIN
	SELECT RAISE(ABORT, 'personal_account_has_enterprise_membership');
END;
--> statement-breakpoint
CREATE TRIGGER `account_profiles_enterprise_no_personal_space`
BEFORE UPDATE OF `account_type` ON `account_profiles`
WHEN NEW.`account_type` = 'enterprise'
  AND EXISTS (
	SELECT 1 FROM `personal_memory_spaces`
	WHERE `user_id` = NEW.`user_id` AND `status` <> 'deleted'
  )
BEGIN
	SELECT RAISE(ABORT, 'enterprise_account_has_personal_space');
END;
--> statement-breakpoint
CREATE TRIGGER `organization_members_no_personal_activation`
BEFORE UPDATE OF `status` ON `organization_members`
WHEN NEW.`status` = 'active'
  AND EXISTS (
	SELECT 1 FROM `account_profiles`
	WHERE `user_id` = NEW.`user_id` AND `account_type` = 'personal'
  )
BEGIN
	SELECT RAISE(ABORT, 'personal_account_enterprise_activation');
END;
--> statement-breakpoint
CREATE TRIGGER `organization_members_no_personal_active_insert`
BEFORE INSERT ON `organization_members`
WHEN NEW.`status` = 'active'
  AND EXISTS (
	SELECT 1 FROM `account_profiles`
	WHERE `user_id` = NEW.`user_id` AND `account_type` = 'personal'
  )
BEGIN
	SELECT RAISE(ABORT, 'personal_account_enterprise_activation');
END;
--> statement-breakpoint
CREATE TRIGGER `personal_memory_spaces_require_personal_account`
BEFORE INSERT ON `personal_memory_spaces`
WHEN NOT EXISTS (
	SELECT 1 FROM `account_profiles`
	WHERE `user_id` = NEW.`user_id` AND `account_type` = 'personal' AND `status` = 'active'
  )
BEGIN
	SELECT RAISE(ABORT, 'personal_space_requires_personal_account');
END;
