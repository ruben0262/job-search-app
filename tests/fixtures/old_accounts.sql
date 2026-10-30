-- Accounts at the final Postgres schema (tag model-on-postgres, Alembic 3c7d1e9a5b20), for tests/test_copy_accounts.py.
-- Includes what must not be copied: a personal Telegram bot key, an old-model notification, a setup-agent
-- chat, a setting nothing reads, a posting.
INSERT INTO plans (id, name) VALUES ('free', 'Free') ON CONFLICT DO NOTHING;
INSERT INTO users (id, email, password_hash, status, email_verified_at, full_name, links, processing_consent_at,
  processing_consent_version, onboarding_completed_at, timezone, active, is_admin, limits, telegram_status,
  telegram_chat_id, model_version, profile_generation, profile, suggestions, wrapped_dek, rubric_md, plan_id, linkedin_interval_min, profile_schema_version)
VALUES
 ('11111111-1111-1111-1111-111111111111', 'alice@example.com', '$argon2id$v=19$m=65536,t=3,p=4$abc$def', 'active',
  now() - interval '30 days', 'Alice Example', '[{"label":"LinkedIn","url":"https://linkedin.com/in/alice"}]',
  now() - interval '29 days', '2026-08', now() - interval '29 days', 'Europe/Budapest', true, true,
  '{"keywords": 40, "label_pool": 300}', 'connected', '424242', 7, 3, '{"skills": ["python"]}',
  '{"green": [], "red": [], "dismissed": ["java"]}', '\xdeadbeef', 'my rubric', 'free', 240, 1),
 ('22222222-2222-2222-2222-222222222222', 'bob@example.com', '$argon2id$v=19$m=65536,t=3,p=4$ghi$jkl', 'pending_verification',
  null, null, null, null, null, null, 'UTC', false, false, '{}', 'unconfigured', null, 0, 0, null, null, null, null, 'free', 240, 1);
INSERT INTO user_config (user_id, config, schema_version) VALUES
 ('11111111-1111-1111-1111-111111111111', '{"sections": [{"platform": "all", "keywords": ["backend engineer"]}]}', 2);
INSERT INTO user_secrets (user_id, kind, ciphertext, nonce, last_4) VALUES
 ('11111111-1111-1111-1111-111111111111', 'claude_api', '\x0102', '\x0304', 'abcd'),
 ('11111111-1111-1111-1111-111111111111', 'telegram_bot', '\x0506', '\x0708', 'wxyz');
INSERT INTO user_documents (id, user_id, kind, filename, text, text_sha256, chars) VALUES
 (5, '11111111-1111-1111-1111-111111111111', 'cv', 'alice.pdf', 'Alice Example, backend engineer', 'sha-a', 31);
INSERT INTO user_profile_files (user_id, name, content, kind, filled) VALUES
 ('11111111-1111-1111-1111-111111111111', 'context.md', '# Alice', 'context', true);
INSERT INTO push_subscriptions (endpoint, user_id, p256dh, auth) VALUES
 ('https://push.example/abc', '11111111-1111-1111-1111-111111111111', 'p256', 'auth');
INSERT INTO proxy_credentials (user_id, secret_ref, mb_used_today, status) VALUES
 ('11111111-1111-1111-1111-111111111111', '11111111-1111-1111-1111-111111111111:proxy', 12.5, 'healthy');
INSERT INTO user_notifications (id, user_id, event, level, title, body) VALUES
 (9, '11111111-1111-1111-1111-111111111111', 'telegram.broken', 'error', 'Telegram delivery stopped', ''),
 (10, '11111111-1111-1111-1111-111111111111', 'model.trained', 'info', 'Your model was retrained', '');
INSERT INTO setup_conversations (id, user_id, title) VALUES
 ('33333333-3333-3333-3333-333333333333', '11111111-1111-1111-1111-111111111111', 'Help'),
 ('44444444-4444-4444-4444-444444444444', '11111111-1111-1111-1111-111111111111', 'Setup');
INSERT INTO setup_messages (id, user_id, conversation_id, role, content, status, agent, tool_calls) VALUES
 (20, '11111111-1111-1111-1111-111111111111', '33333333-3333-3333-3333-333333333333', 'user', 'How do words work?', 'done', 'support', null),
 (21, '11111111-1111-1111-1111-111111111111', '33333333-3333-3333-3333-333333333333', 'assistant', 'Green words', 'streaming', 'support', '{"steps": []}'),
 (22, '11111111-1111-1111-1111-111111111111', '44444444-4444-4444-4444-444444444444', 'assistant', 'Setup chat', 'done', 'setup-director', null);
INSERT INTO platform_settings (key, value) VALUES ('support_chat_enabled', 'true'), ('embed_batch_size', '64');
INSERT INTO operator_keywords (id, keyword, location) VALUES (3, 'nurse', null);
INSERT INTO ats_tokens (ats, token) VALUES ('greenhouse', 'acme'), ('lever', 'globex');
INSERT INTO corpus (url, source, title, describe_failures, schema_version) VALUES ('https://jobs/1', 'test', 'Backend Engineer', 0, 1);
CREATE TABLE IF NOT EXISTS lane_pauses (lane TEXT PRIMARY KEY, paused_at TIMESTAMPTZ NOT NULL DEFAULT now(), reason TEXT);
INSERT INTO lane_pauses (lane, reason) VALUES ('docgen', 'maintenance');
