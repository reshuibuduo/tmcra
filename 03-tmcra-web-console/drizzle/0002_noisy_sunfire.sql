CREATE TABLE `early_access_requests` (
	`id` text PRIMARY KEY NOT NULL,
	`email_normalized` text NOT NULL,
	`email_display` text NOT NULL,
	`source` text DEFAULT 'website' NOT NULL,
	`status` text DEFAULT 'new' NOT NULL,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	CONSTRAINT "early_access_requests_status_check" CHECK("early_access_requests"."status" IN ('new', 'contacted', 'qualified', 'closed'))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `early_access_requests_email_uq` ON `early_access_requests` (`email_normalized`);--> statement-breakpoint
CREATE INDEX `early_access_requests_status_created_idx` ON `early_access_requests` (`status`,`created_at`);