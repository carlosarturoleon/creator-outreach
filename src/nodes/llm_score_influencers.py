"""
Node 4b: LLM-powered affiliate-fit scoring via Anthropic Message Batches API.

Runs after the deterministic score_influencers node.
Sends all scored channels to Claude in a single batch, waits for results,
enriches each influencer dict with llm_score and llm_rationale, drops
weak-fit channels (llm_score < LLM_SCORE_FLOOR), and re-sorts by
llm_score (primary) then composite_score (secondary).
"""
from src.config import settings
from src.db.database import Database
from src.logger import get_logger
from src.state import GraphState
from src.tools.batch_scorer_client import (
    build_scorer_requests,
    fetch_scorer_results,
    submit_batch,
    wait_for_batch,
)

log = get_logger(__name__)

LLM_SCORE_FLOOR = 4   # Channels scoring below this are dropped from the pipeline


def llm_score_influencers(state: GraphState) -> dict:
    """
    Node 4b: Enrich scored_influencers with Claude affiliate-fit scores via Batch API.

    Reads from state (scored_influencers + enriched_channels).
    Persists llm_score and llm_rationale to DB.
    Drops channels with llm_score < LLM_SCORE_FLOOR.
    Sorts: primary=llm_score desc, secondary=composite_score desc.
    """
    influencers = state.get("scored_influencers", [])
    enriched_map: dict[str, dict] = {
        ch["channel_id"]: ch for ch in state.get("enriched_channels", [])
    }
    db = Database()
    errors: list[str] = []

    if not influencers:
        log.info("llm_score_influencers — no influencers to score, skipping")
        return {"scored_influencers": [], "error_log": [], "current_phase": "llm_scoring_complete"}

    log.info("llm_score_influencers START — submitting %d channels to Claude batch", len(influencers))

    # Build and submit batch
    requests = build_scorer_requests(
        influencers=influencers,
        enriched_map=enriched_map,
        model=settings.claude_model,
    )

    try:
        batch_id = submit_batch(requests)
    except Exception as e:
        err = f"[llm_score_influencers] Batch submission failed: {e}"
        log.error(err)
        # Fallback: pass all influencers through with llm_score=None (no filtering)
        return {
            "scored_influencers": influencers,
            "error_log": [err],
            "current_phase": "llm_scoring_complete",
        }

    # Poll until done
    try:
        wait_for_batch(batch_id)
    except TimeoutError as e:
        err = f"[llm_score_influencers] {e}"
        log.error(err)
        errors.append(err)
        # Attempt partial result fetch even on timeout

    # Retrieve results
    try:
        results = fetch_scorer_results(batch_id)
    except Exception as e:
        err = f"[llm_score_influencers] Result fetch failed: {e}"
        log.error(err)
        return {
            "scored_influencers": influencers,
            "error_log": errors + [err],
            "current_phase": "llm_scoring_complete",
        }

    # Enrich influencers and persist
    enriched: list[dict] = []
    for influencer in influencers:
        cid = influencer["channel_id"]
        result = results.get(cid, {})

        llm_score = result.get("llm_score", 0)
        llm_rationale = result.get("llm_rationale", "LLM score unavailable.")
        success = result.get("success", False)

        if not success:
            errors.append(f"[llm_score_influencers] Batch failed for {cid}: {llm_rationale}")

        updated = {**influencer, "llm_score": llm_score, "llm_rationale": llm_rationale}
        enriched.append(updated)

        try:
            db.upsert_scored_influencer(updated)
        except Exception as e:
            err_msg = f"[llm_score_influencers] DB upsert failed for {cid}: {e}"
            log.error(err_msg)
            errors.append(err_msg)

        log.info(
            "  %s — llm_score=%d  %s",
            influencer["channel_title"], llm_score, llm_rationale[:80],
        )

    # Apply floor filter
    before_filter = len(enriched)
    passed = [ch for ch in enriched if ch.get("llm_score", 0) >= LLM_SCORE_FLOOR]
    dropped = before_filter - len(passed)
    if dropped:
        log.info(
            "llm_score_influencers — dropped %d/%d channels (llm_score < %d)",
            dropped, before_filter, LLM_SCORE_FLOOR,
        )

    # Sort: llm_score primary, composite_score secondary (both descending)
    passed.sort(key=lambda x: (x.get("llm_score", 0), x.get("composite_score", 0)), reverse=True)

    log.info(
        "llm_score_influencers DONE — %d/%d passed LLM filter, %d errors",
        len(passed), before_filter, len(errors),
    )

    return {
        "scored_influencers": passed,
        "error_log": errors,
        "current_phase": "llm_scoring_complete",
    }
