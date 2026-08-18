CREATE TABLE `device_authorizations` (
	`id` text PRIMARY KEY NOT NULL,
	`device_code_hash` text NOT NULL,
	`user_code_hash` text NOT NULL,
	`code_challenge` text NOT NULL,
	`code_challenge_method` text DEFAULT 'S256' NOT NULL,
	`client_name` text DEFAULT 'Codex' NOT NULL,
	`status` text DEFAULT 'pending' NOT NULL,
	`interval_seconds` integer DEFAULT 5 NOT NULL,
	`poll_count` integer DEFAULT 0 NOT NULL,
	`last_polled_at` integer,
	`approved_by_user_id` text,
	`personal_space_id` text,
	`token_ciphertext` text,
	`token_iv` text,
	`created_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`updated_at` integer DEFAULT (unixepoch() * 1000) NOT NULL,
	`expires_at` integer NOT NULL,
	`approved_at` integer,
	`claimed_at` integer,
	FOREIGN KEY (`approved_by_user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE set null,
	FOREIGN KEY (`personal_space_id`) REFERENCES `personal_memory_spaces`(`id`) ON UPDATE no action ON DELETE set null,
	CONSTRAINT "device_authorizations_status_check" CHECK("device_authorizations"."status" IN ('pending', 'authorizing', 'approved', 'denied', 'claimed', 'expired')),
	CONSTRAINT "device_authorizations_pkce_method_check" CHECK("device_authorizations"."code_challenge_method" = 'S256'),
	CONSTRAINT "device_authorizations_interval_check" CHECK("device_authorizations"."interval_seconds" BETWEEN 1 AND 60),
	CONSTRAINT "device_authorizations_token_pair_check" CHECK(("device_authorizations"."token_ciphertext" IS NULL) = ("device_authorizations"."token_iv" IS NULL))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `device_authorizations_device_hash_uq` ON `device_authorizations` (`device_code_hash`);--> statement-breakpoint
CREATE UNIQUE INDEX `device_authorizations_user_hash_uq` ON `device_authorizations` (`user_code_hash`);--> statement-breakpoint
CREATE INDEX `device_authorizations_status_expires_idx` ON `device_authorizations` (`status`,`expires_at`);--> statement-breakpoint
CREATE INDEX `device_authorizations_space_created_idx` ON `device_authorizations` (`personal_space_id`,`created_at`);--> statement-breakpoint
CREATE TABLE `device_connections` (
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
	CONSTRAINT "device_connections_provider_check" CHECK("device_connections"."provider" = 'codex'),
	CONSTRAINT "device_connections_status_check" CHECK("device_connections"."status" IN ('active', 'revoked', 'expired')),
	CONSTRAINT "device_connections_permissions_json_check" CHECK(json_valid("device_connections"."permissions_json"))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `device_connections_authorization_uq` ON `device_connections` (`authorization_id`);--> statement-breakpoint
CREATE UNIQUE INDEX `device_connections_token_id_uq` ON `device_connections` (`token_id`);--> statement-breakpoint
CREATE INDEX `device_connections_space_status_idx` ON `device_connections` (`personal_space_id`,`status`,`created_at`);--> statement-breakpoint
CREATE INDEX `device_connections_user_status_idx` ON `device_connections` (`user_id`,`status`,`created_at`);