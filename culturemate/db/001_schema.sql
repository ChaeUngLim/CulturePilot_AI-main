-- CultureMate 스키마
-- 설계 축: (1) 사실(places/snapshots)  (2) 계획(plans)  (3) 경험(visits/edits)
--          (4) 검색 인덱스(experience_embeddings)  (5) 집계(taste_profiles)
-- 경험은 원본 그대로 남기고, 검색용 표현은 experience_embeddings에 파생 저장한다.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ------------------------------------------------------------------ 사용자
CREATE TABLE IF NOT EXISTS users (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    email        text UNIQUE,
    display_name text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- UR-01 카드 방식 초기 취향 등록
CREATE TABLE IF NOT EXISTS preference_cards (
    id         uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject    text NOT NULL,                    -- 카테고리 또는 place_id
    verdict    text NOT NULL CHECK (verdict IN
                 ('recommend','dislike','interested','not_interested')),
    experienced boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, subject)
);

-- ------------------------------------------------------------------ 장소(사실)
CREATE TABLE IF NOT EXISTS places (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_key text UNIQUE,                    -- 소스 시스템의 안정 식별자
    name         text NOT NULL,
    kind         text NOT NULL DEFAULT 'venue',  -- event|venue|food|cafe|shop|park
    category     text,
    address      text,
    region       text,
    lat          double precision,
    lng          double precision,
    indoor       boolean,
    official_url text,
    dwell_min    integer,          -- 예상 체류시간(분). 일정 편성의 입력
    parking      text,             -- free | paid | nearby | none | unknown
    parking_note text,
    curated      boolean NOT NULL DEFAULT false,  -- 카탈로그로 등록된 장소인지
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_places_region  ON places (region);
CREATE INDEX IF NOT EXISTS idx_places_geo     ON places (lat, lng);
CREATE INDEX IF NOT EXISTS idx_places_name_trgm ON places USING gin (name gin_trgm_ops);

-- 재방문 diff의 기준점. 검증할 때마다 새 버전을 남긴다.
CREATE TABLE IF NOT EXISTS place_snapshots (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    place_id    uuid NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    payload     jsonb NOT NULL,                  -- hours/fee/reservation/parking/program
    source_url  text,
    verified_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_snapshots_place ON place_snapshots (place_id, verified_at DESC);

-- ------------------------------------------------------------------ 계획
CREATE TABLE IF NOT EXISTS plans (
    id         text PRIMARY KEY,                 -- Itinerary.id
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_date  date,
    version    integer NOT NULL DEFAULT 1,
    status     text NOT NULL DEFAULT 'active',   -- draft|active|archived
    payload    jsonb NOT NULL,                   -- Itinerary 전체
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_plans_user_date ON plans (user_id, plan_date DESC);

-- 수정 행동. 별점보다 강한 개인화 신호이므로 원자 단위로 남긴다.
CREATE TABLE IF NOT EXISTS plan_edits (
    id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id       text REFERENCES plans(id) ON DELETE CASCADE,
    action        text NOT NULL,                 -- remove|replace|reorder|dwell_up|...
    from_place_id uuid REFERENCES places(id),
    to_place_id   uuid REFERENCES places(id),
    detail        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_plan_edits_user ON plan_edits (user_id, created_at DESC);

-- ------------------------------------------------------------------ 경험
CREATE TABLE IF NOT EXISTS visits (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    place_id    uuid NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    plan_id     text REFERENCES plans(id) ON DELETE SET NULL,
    snapshot_id uuid REFERENCES place_snapshots(id),
    visited_at  timestamptz NOT NULL DEFAULT now(),
    rating      double precision,
    review      text,
    friction    text[] NOT NULL DEFAULT '{}',    -- parking|crowding|accessibility|...
    companions  text,
    transport   text,
    dwell_min   integer,
    travel_min  integer,
    is_revisit  boolean NOT NULL DEFAULT false,
    photos      text[] NOT NULL DEFAULT '{}',
    meta        jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_visits_user_place ON visits (user_id, place_id, visited_at DESC);

-- ------------------------------------------------------- 아카이브 검색 인덱스
-- 방문/리뷰/수정행동/메모를 '검색 가능한 한 문장'으로 정규화한 파생 테이블.
CREATE TABLE IF NOT EXISTS experience_embeddings (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_type text NOT NULL CHECK (source_type IN
                  ('visit','review','plan_edit','note','profile')),
    source_id   text NOT NULL,
    place_id    uuid REFERENCES places(id) ON DELETE SET NULL,
    summary     text NOT NULL,
    tags        text[] NOT NULL DEFAULT '{}',
    friction    text[] NOT NULL DEFAULT '{}',
    sentiment   double precision NOT NULL DEFAULT 0,
    rating      double precision,
    occurred_at timestamptz,
    meta        jsonb NOT NULL DEFAULT '{}'::jsonb,  -- region/companions/transport/season
    embedding   vector(1024) NOT NULL,
    ts          tsvector,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_type, source_id)
);

-- dense: HNSW + 코사인. 사용자별 필터가 항상 붙으므로 partial 대신 복합 필터 인덱스 병행.
CREATE INDEX IF NOT EXISTS idx_exp_embedding
    ON experience_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
-- lexical: 하이브리드 검색의 두 번째 랭커
CREATE INDEX IF NOT EXISTS idx_exp_ts       ON experience_embeddings USING gin (ts);
CREATE INDEX IF NOT EXISTS idx_exp_user     ON experience_embeddings (user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_exp_friction ON experience_embeddings USING gin (friction);
CREATE INDEX IF NOT EXISTS idx_exp_meta     ON experience_embeddings USING gin (meta jsonb_path_ops);

-- ------------------------------------------------------------------ 집계·HITL
CREATE TABLE IF NOT EXISTS taste_profiles (
    user_id    uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    profile    jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hitl_decisions (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    advisory_id text NOT NULL,
    option_id   text NOT NULL,
    note        text,
    decided_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, advisory_id)
);

-- 외부 API 응답 캐시(운영시간·좌표·날씨). TTL은 애플리케이션이 관리.
CREATE TABLE IF NOT EXISTS api_cache (
    key        text PRIMARY KEY,
    payload    jsonb NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_cache_exp ON api_cache (expires_at);

-- ------------------------------------------------------- 즐겨찾기(내 컬렉션)
-- 규칙으로 자동 생성되는 큐레이션(memory/curation.py)과 다른 축이다.
-- 그쪽은 "기록에서 뽑아낸 것", 이쪽은 "내가 직접 담은 것"이라 섞으면 안 된다.
-- 자동 테마는 방문 기록이 바뀌면 사라지지만, 내가 담은 건 지우기 전까지 남는다.
CREATE TABLE IF NOT EXISTS user_collections (
    id         uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      text NOT NULL,
    emoji      text NOT NULL DEFAULT '⭐',
    subtitle   text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, title)
);

CREATE TABLE IF NOT EXISTS user_collection_places (
    collection_id uuid NOT NULL REFERENCES user_collections(id) ON DELETE CASCADE,
    place_id      uuid NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    -- 왜 담았는지. 자동 테마의 reason 과 같은 자리를 쓴다.
    note          text,
    added_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (collection_id, place_id)
);
CREATE INDEX IF NOT EXISTS idx_ucp_place ON user_collection_places (place_id);
