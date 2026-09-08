ALTER TABLE provisioned_plans ADD COLUMN regulation_policy_json TEXT NOT NULL
DEFAULT '{"version":"conversation-regulation/v1","allowed_strategies":["repeat-instruction","simplify-language","give-ordered-hint","redirect-gently","validate-emotion","take-short-pause"],"max_consecutive_regulation_turns":4}';
