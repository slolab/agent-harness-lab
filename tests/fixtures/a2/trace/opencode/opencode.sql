CREATE TABLE `message` (
          `id` text PRIMARY KEY,
          `session_id` text NOT NULL,
          `time_created` integer NOT NULL,
          `time_updated` integer NOT NULL,
          `data` text NOT NULL,
          CONSTRAINT `fk_message_session_id_session_id_fk` FOREIGN KEY (`session_id`) REFERENCES `session`(`id`) ON DELETE CASCADE
        );
CREATE TABLE `part` (
          `id` text PRIMARY KEY,
          `message_id` text NOT NULL,
          `session_id` text NOT NULL,
          `time_created` integer NOT NULL,
          `time_updated` integer NOT NULL,
          `data` text NOT NULL,
          CONSTRAINT `fk_part_message_id_message_id_fk` FOREIGN KEY (`message_id`) REFERENCES `message`(`id`) ON DELETE CASCADE
        );
CREATE TABLE `session` (
          `id` text PRIMARY KEY,
          `project_id` text NOT NULL,
          `workspace_id` text,
          `parent_id` text,
          `slug` text NOT NULL,
          `directory` text NOT NULL,
          `path` text,
          `title` text NOT NULL,
          `version` text NOT NULL,
          `share_url` text,
          `summary_additions` integer,
          `summary_deletions` integer,
          `summary_files` integer,
          `summary_diffs` text,
          `metadata` text,
          `cost` real DEFAULT 0 NOT NULL,
          `tokens_input` integer DEFAULT 0 NOT NULL,
          `tokens_output` integer DEFAULT 0 NOT NULL,
          `tokens_reasoning` integer DEFAULT 0 NOT NULL,
          `tokens_cache_read` integer DEFAULT 0 NOT NULL,
          `tokens_cache_write` integer DEFAULT 0 NOT NULL,
          `revert` text,
          `permission` text,
          `agent` text,
          `model` text,
          `time_created` integer NOT NULL,
          `time_updated` integer NOT NULL,
          `time_compacting` integer,
          `time_archived` integer,
          CONSTRAINT `fk_session_project_id_project_id_fk` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE
        );
INSERT INTO "message" VALUES('msg_0d4f5d9c2001kp6QAWgFhYy4sb','ses_f2b0a267cffejImOtxurPkcfpq',1790279276995,1790279288068,'{"role":"user","time":{"created":1790279276995},"agent":"build","model":{"providerID":"openrouter","modelID":"deepseek/deepseek-v4.1-flash"},"summary":{"diffs":[]}}');
INSERT INTO "message" VALUES('msg_0d4f5dc44001eVRf7YSDHTeoS8','ses_f2b0a267cffejImOtxurPkcfpq',1790279277636,1790279288051,'{"parentID":"msg_0d4f5d9c2001kp6QAWgFhYy4sb","role":"assistant","mode":"build","agent":"build","path":{"cwd":"/workspace","root":"/"},"cost":0.000902676,"tokens":{"total":7483,"input":5594,"output":60,"reasoning":37,"cache":{"write":0,"read":1792}},"modelID":"deepseek/deepseek-v4.1-flash","providerID":"openrouter","time":{"created":1790279277636,"completed":1790279288050},"finish":"tool-calls"}');
INSERT INTO "message" VALUES('msg_0d4f604f6001tQ9O3o7iISsiv6','ses_f2b0a267cffejImOtxurPkcfpq',1790279288054,1790279289950,'{"parentID":"msg_0d4f5d9c2001kp6QAWgFhYy4sb","role":"assistant","mode":"build","agent":"build","path":{"cwd":"/workspace","root":"/"},"cost":0.000035172,"tokens":{"total":7501,"input":74,"output":3,"reasoning":0,"cache":{"write":0,"read":7424}},"modelID":"deepseek/deepseek-v4.1-flash","providerID":"openrouter","time":{"created":1790279288054,"completed":1790279289948},"finish":"stop"}');
INSERT INTO "message" VALUES('msg_0d4f6af250015ev66rQJWGRvnX','ses_f2b0a267cffejImOtxurPkcfpq',1790279331621,1790279332160,'{"role":"user","time":{"created":1790279331621},"agent":"build","model":{"providerID":"openrouter","modelID":"deepseek/deepseek-v4.1-flash"},"summary":{"diffs":[]}}');
INSERT INTO "message" VALUES('msg_0d4f6b1170013ntf6B19L0lVpE','ses_f2b0a267cffejImOtxurPkcfpq',1790279332119,1790279334653,'{"parentID":"msg_0d4f6af250015ev66rQJWGRvnX","role":"assistant","mode":"build","agent":"build","path":{"cwd":"/workspace","root":"/"},"cost":0.000042222,"tokens":{"total":7530,"input":97,"output":9,"reasoning":0,"cache":{"write":0,"read":7424}},"modelID":"deepseek/deepseek-v4.1-flash","providerID":"openrouter","time":{"created":1790279332119,"completed":1790279334652},"finish":"stop"}');
INSERT INTO "part" VALUES('prt_0d4f5d9ca001pZHEeC1db69MLG','msg_0d4f5d9c2001kp6QAWgFhYy4sb','ses_f2b0a267cffejImOtxurPkcfpq',1790279277619,1790279277621,'{"type":"text","text":"Remember this nonce for later: b4f939b581bc. Do not write the nonce to any file.\nUse a tool to create the file /workspace/hello.txt containing the word hello. Then reply with ''ready''.\n"}');
INSERT INTO "part" VALUES('prt_0d4f5f213001olTU8hkCB0FnlA','msg_0d4f5dc44001eVRf7YSDHTeoS8','ses_f2b0a267cffejImOtxurPkcfpq',1790279283220,1790279283223,'{"type":"step-start"}');
INSERT INTO "part" VALUES('prt_0d4f5f21a0017MV3J2NjWeS3PC','msg_0d4f5dc44001eVRf7YSDHTeoS8','ses_f2b0a267cffejImOtxurPkcfpq',1790279283226,1790279288011,'{"type":"reasoning","text":"The user asks me to remember a nonce and not write it to any file. Then create /workspace/hello.txt with \"hello\" and reply \"ready\". Straightforward.","time":{"start":1790279283226,"end":1790279288009},"metadata":{"openrouter":{"reasoning_details":[{"type":"reasoning.text","text":"The user asks me to remember a nonce and not write it to any file. Then create /workspace/hello.txt with \"hello\" and reply \"ready\". Straightforward.","format":"unknown","index":0}]}}}');
INSERT INTO "part" VALUES('prt_0d4f5fbfb001gqbV1K2WSZtdrH','msg_0d4f5dc44001eVRf7YSDHTeoS8','ses_f2b0a267cffejImOtxurPkcfpq',1790279285755,1790279288039,'{"type":"tool","tool":"write","callID":"chatcmpl-tool-93e851de8f5041fa","state":{"status":"completed","input":{"filePath":"/workspace/hello.txt","content":"hello"},"output":"Wrote file successfully.","metadata":{"diagnostics":{},"filepath":"/workspace/hello.txt","exists":false,"truncated":false},"title":"workspace/hello.txt","time":{"start":1790279288001,"end":1790279288038}},"metadata":{"openrouter":{"reasoning_details":[{"type":"reasoning.text","text":"The user asks me to remember a nonce and not write it to any file. Then create /workspace/hello.txt with \"hello\" and reply \"ready\". Straightforward.","format":"unknown","index":0}]}}}');
INSERT INTO "part" VALUES('prt_0d4f604eb001D8QGTzPkXJismR','msg_0d4f5dc44001eVRf7YSDHTeoS8','ses_f2b0a267cffejImOtxurPkcfpq',1790279288043,1790279288044,'{"reason":"tool-calls","type":"step-finish","tokens":{"total":7483,"input":5594,"output":60,"reasoning":37,"cache":{"write":0,"read":1792}},"cost":0.000902676}');
INSERT INTO "part" VALUES('prt_0d4f60c3f001thwm60vAYYDS2Q','msg_0d4f604f6001tQ9O3o7iISsiv6','ses_f2b0a267cffejImOtxurPkcfpq',1790279289919,1790279289921,'{"type":"step-start"}');
INSERT INTO "part" VALUES('prt_0d4f60c45001Klzmqwv3bRe9f4','msg_0d4f604f6001tQ9O3o7iISsiv6','ses_f2b0a267cffejImOtxurPkcfpq',1790279289925,1790279289933,'{"type":"text","text":"ready","time":{"start":1790279289925,"end":1790279289931}}');
INSERT INTO "part" VALUES('prt_0d4f60c51001bC33L8Mf7Hl5er','msg_0d4f604f6001tQ9O3o7iISsiv6','ses_f2b0a267cffejImOtxurPkcfpq',1790279289937,1790279289940,'{"reason":"stop","type":"step-finish","tokens":{"total":7501,"input":74,"output":3,"reasoning":0,"cache":{"write":0,"read":7424}},"cost":0.000035172}');
INSERT INTO "part" VALUES('prt_0d4f6af27001MJxpJN4emzc7ov','msg_0d4f6af250015ev66rQJWGRvnX','ses_f2b0a267cffejImOtxurPkcfpq',1790279332101,1790279332103,'{"type":"text","text":"What is the nonce I gave you earlier? Reply with the nonce only.\n"}');
INSERT INTO "part" VALUES('prt_0d4f6bac0001hf73Qsrl0fbWFS','msg_0d4f6b1170013ntf6B19L0lVpE','ses_f2b0a267cffejImOtxurPkcfpq',1790279334592,1790279334595,'{"type":"step-start"}');
INSERT INTO "part" VALUES('prt_0d4f6bac8001jHzkx9xNBDtbOo','msg_0d4f6b1170013ntf6B19L0lVpE','ses_f2b0a267cffejImOtxurPkcfpq',1790279334600,1790279334631,'{"type":"text","text":"b4f939b581bc","time":{"start":1790279334600,"end":1790279334628}}');
INSERT INTO "part" VALUES('prt_0d4f6baf1001yTjqSmwLQK72B0','msg_0d4f6b1170013ntf6B19L0lVpE','ses_f2b0a267cffejImOtxurPkcfpq',1790279334642,1790279334644,'{"reason":"stop","type":"step-finish","tokens":{"total":7530,"input":97,"output":9,"reasoning":0,"cache":{"write":0,"read":7424}},"cost":0.000042222}');
INSERT INTO "session" VALUES('ses_f2b0a267cffejImOtxurPkcfpq','global',NULL,NULL,'jolly-comet','/workspace','workspace','New session - 2026-09-24T19:47:56.932Z','1.18.32',NULL,0,0,0,NULL,NULL,0.00098007,5765,72,37,16640,0,NULL,'[{"permission":"question","pattern":"*","action":"deny"},{"permission":"plan_enter","pattern":"*","action":"deny"},{"permission":"plan_exit","pattern":"*","action":"deny"}]','build','{"id":"deepseek/deepseek-v4.1-flash","providerID":"openrouter","variant":"default"}',1790279276932,1790279334677,NULL,NULL);
