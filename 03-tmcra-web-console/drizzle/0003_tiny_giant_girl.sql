PRAGMA foreign_keys=OFF;--> statement-breakpoint
CREATE TABLE `__new_early_access_requests` (
	`id` text PRIMARY KEY NOT NULL,
	`email_normalized` text NOT NULL,
	`email_display` text NOT NULL,
	`contact_name` text DEFAULT '' NOT NULL,
	`company_name` text DEFAULT '' NOT NULL,
	`industry` text DEFAULT '' NOT NULL,
	`company_size` text DEFAULT '' NOT NULL,
	`primary_use_case` text DEFAULT '' NOT NULL,
	`platforms_json` text DEFAULT '[]' NOT NULL,
	`timeline` text DEFAULT '' NOT NULL,
	`source` text DEFAULT 'website' NOT NULL,
	`status` text DEFAULT 'new' NOT NULL,
	`review_note` text DEFAULT '' NOT NULL,
	`last_reviewed_by` text,
	`last_reviewed_at` integer,
	`version` integer DEFAULT 1 NOT NULL,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	CONSTRAINT "early_access_requests_status_check" CHECK("__new_early_access_requests"."status" IN ('new', 'contacted', 'qualified', 'closed')),
	CONSTRAINT "early_access_requests_platforms_json_check" CHECK(json_valid("__new_early_access_requests"."platforms_json"))
);
--> statement-breakpoint
INSERT INTO `__new_early_access_requests`("id", "email_normalized", "email_display", "source", "status", "created_at", "updated_at") SELECT "id", "email_normalized", "email_display", "source", "status", "created_at", "updated_at" FROM `early_access_requests`;--> statement-breakpoint
DROP TABLE `early_access_requests`;--> statement-breakpoint
ALTER TABLE `__new_early_access_requests` RENAME TO `early_access_requests`;--> statement-breakpoint
PRAGMA foreign_keys=ON;--> statement-breakpoint
CREATE UNIQUE INDEX `early_access_requests_email_uq` ON `early_access_requests` (`email_normalized`);--> statement-breakpoint
CREATE INDEX `early_access_requests_status_created_idx` ON `early_access_requests` (`status`,`created_at`);
