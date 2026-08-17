"""모델별 실제 지연을 재서 역할에 맞는 모델을 고른다.

    docker compose exec api python scripts/bench_models.py

모델 크기(8B/70B)는 품질과 속도의 대리 지표일 뿐이다. 같은 크기라도 서빙 최적화에
따라 몇 배씩 차이가 나고, 카탈로그도 계속 바뀐다. 그래서 '어느 모델이 좋다'를
외우는 대신, 이 프로젝트가 실제로 시키는 일을 그대로 시켜 보고 재는 편이 정확하다.

측정 작업
  · router : 발화 → RequestType + 조건 (구조화 출력, 짧고 잦다)
  · writer : 일정 설명 문장 생성 (길고 드물다)
"""
from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("LLM_BACKEND", "nim")

from app.config import get_settings
from app.schemas import RequestType

# 후보 목록. build.nvidia.com 카탈로그에서 쓸 만한 것들.
# 계정에 없거나 이름이 바뀐 모델은 '실패'로 표시되고 넘어간다.
CANDIDATES = [
    "meta/llama-3.1-8b-instruct",
    "meta/llama-3.3-70b-instruct",
    "nvidia/llama-3.1-nemotron-nano-8b-v1",
    "nvidia/llama-3.3-nemotron-super-49b-v1",
    "qwen/qwen2.5-7b-instruct",
    "mistralai/mistral-small-24b-instruct",
    "google/gemma-3-27b-it",
]

# OpenAI 키가 있으면 비교군에 넣는다 — 유료지만 라우팅 지연의 기준선이 된다
if get_settings().openai_api_key:
    CANDIDATES += ["openai:gpt-4o-mini"]

ROUTER_PROMPT = [
    {"role": "system",
     "content": "사용자 발화를 분류하고 조건을 뽑아라. "
                "request_type 은 plan_create / place_recommend / archive_query / "
                "taste_report 중 하나. region 은 '서울 서대문구' 형식."},
    {"role": "user", "content": "서대문구에서 8월 11일 문화생활 일정 짜줘"},
]
WRITER_PROMPT = [
    {"role": "system", "content": "일정을 한국어로 3줄 이내로 소개하라."},
    {"role": "user",
     "content": "11:00 대림미술관(80분), 13:00 땡스북스(60분), 15:00 성수연방(60분)"},
]
ROUNDS = 3


class Route(__import__("pydantic").BaseModel):
    request_type: RequestType = RequestType.PLAN_CREATE
    region: str | None = None


async def _timed(fn) -> tuple[float | None, str]:
    t0 = time.perf_counter()
    try:
        out = await asyncio.wait_for(fn(), timeout=30)
        return time.perf_counter() - t0, str(out)[:60]
    except TimeoutError:
        return None, "30초 초과"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:70]


def _make_llm(spec: str):
    """'openai:gpt-4o-mini' 처럼 공급자를 섞어 비교할 수 있게 한다."""
    s = get_settings()
    backend, _, name = spec.partition(":") if ":" in spec else ("nim", "", spec)
    name = name or spec
    if backend == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=name, temperature=0, api_key=s.openai_api_key)
    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    return ChatNVIDIA(model=name, temperature=0, api_key=s.nvidia_api_key)


async def bench(model: str) -> dict:
    llm = _make_llm(model)

    router_times: list[float] = []
    note = ""
    try:
        chain = llm.with_structured_output(Route)
    except Exception as exc:
        return {"model": model, "error": f"구조화 출력 미지원: {exc}"[:70]}

    for _ in range(ROUNDS):
        dt, msg = await _timed(lambda: chain.ainvoke(ROUTER_PROMPT))
        if dt is None:
            return {"model": model, "error": msg}
        router_times.append(dt)
        note = msg

    writer_dt, _ = await _timed(lambda: llm.ainvoke(WRITER_PROMPT))
    return {
        "model": model,
        "router_p50": statistics.median(router_times),
        "router_min": min(router_times),
        "writer": writer_dt,
        "sample": note,
    }


async def main() -> int:
    s = get_settings()
    if not s.nvidia_api_key:
        print("NVIDIA_API_KEY 가 없습니다.")
        return 1

    print("=" * 72)
    print(f"  후보 {len(CANDIDATES)}개 · 라우팅 {ROUNDS}회 + 서술 1회씩 측정")
    print("=" * 72)

    results = []
    for model in CANDIDATES:
        print(f"\n  {model}")
        r = await bench(model)
        results.append(r)
        if r.get("error"):
            print(f"     ❌ {r['error']}")
        else:
            print(f"     라우팅 {r['router_p50']:.2f}s (최소 {r['router_min']:.2f}s)"
                  f" · 서술 {r['writer']:.2f}s" if r["writer"] else "")
            print(f"     응답: {r['sample']}")

    ok = [r for r in results if not r.get("error")]
    if not ok:
        print("\n  사용 가능한 모델이 없습니다. 키와 모델 이름을 확인하세요.")
        return 1

    ok.sort(key=lambda r: r["router_p50"])
    fastest = ok[0]
    # 서술은 품질이 중요하므로, 라우팅 최속이 아니라 '느리지 않은 것 중 큰 모델'을 고른다
    writer_pool = [r for r in ok if r["writer"] and r["writer"] < 12] or ok
    writer = max(writer_pool, key=lambda r: r["writer"] or 0)

    print("\n" + "=" * 72)
    print("  라우팅 속도 순위")
    for r in ok:
        print(f"    {r['router_p50']:>6.2f}s  {r['model']}")
    print("\n  .env 에 넣을 값 (제안)")
    print(f"    MODEL_ROUTER={fastest['model']}")
    print(f"    MODEL_FAST={fastest['model']}")
    print(f"    MODEL_PLANNER={writer['model']}")
    print(f"    MODEL_WRITER={writer['model']}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
