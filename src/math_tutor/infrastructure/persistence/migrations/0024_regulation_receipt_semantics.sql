ALTER TABLE learner_support_receipts
ADD COLUMN semantic_fingerprint TEXT
CHECK(
 semantic_fingerprint IS NULL OR
 (length(semantic_fingerprint) = 64 AND semantic_fingerprint NOT GLOB '*[^0-9a-f]*')
);
