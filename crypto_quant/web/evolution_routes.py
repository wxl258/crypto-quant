"""
Evolution API Routes — Self-evolution endpoints.
Extracted from routes.py for modularity.
"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel as PydanticModel

router = APIRouter(prefix="/api/evolution")

# Lazy import — heavy modules only loaded on first endpoint call
_evo_v3 = None

def _get_evo_v3():
    global _evo_v3
    if _evo_v3 is None:
        from evolution_v3 import evo_v3 as _mod
        _evo_v3 = _mod
    return _evo_v3


class EvolutionRequest(PydanticModel):
    strategy: str = "trend_follower"
    symbol: str = "BTCUSDT"
    interval: str = "1d"
    population_size: int = 20
    generations: int = 5
    train_days: int = 90
    test_days: int = 30
    auto_approve: bool = False


@router.post("/evolve")
async def evolution_evolve(req: EvolutionRequest):
    try:
        evo = _get_evo_v3()
        result = evo.evolve_params(
            req.strategy, req.symbol, req.interval,
            n_iterations=req.population_size,
            train_days=req.train_days, test_days=req.test_days
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/dashboard")
async def evolution_dashboard():
    evo = _get_evo_v3()
    return evo.get_dashboard_data() if hasattr(evo, 'get_dashboard_data') else {'status': 'ok'}


@router.get("/history")
async def evolution_history():
    evo = _get_evo_v3()
    return {'history': evo.get_evolution_log()}


@router.get("/state")
async def evolution_state_endpoint():
    evo = _get_evo_v3()
    return {'status': 'active', 'log_count': len(evo.get_evolution_log())}


@router.post("/emergency_stop")
async def evolution_emergency_stop(reason: str = Query(default="手动触发")):
    return {'status': 'stopped', 'reason': reason}


@router.post("/resume")
async def evolution_resume():
    return {'status': 'resumed'}


@router.post("/cross_validate")
async def cross_validate(strategy: str = Query(default="trend_follower")):
    evo = _get_evo_v3()
    return evo.cross_validate(strategy)


@router.post("/stress_test")
async def stress_test(strategy: str = Query(default="trend_follower")):
    evo = _get_evo_v3()
    return evo.stress_test(strategy)


@router.post("/sensitivity")
async def sensitivity(strategy: str = Query(default="trend_follower")):
    evo = _get_evo_v3()
    return evo.sensitivity_analysis(strategy)


@router.post("/detect_decay")
async def detect_decay(strategy: str = Query(default="trend_follower")):
    evo = _get_evo_v3()
    return evo.detect_decay(strategy)


@router.post("/ab_test")
async def ab_test(strategy: str = Query(default="trend_follower")):
    evo = _get_evo_v3()
    result = evo.evolve_params(strategy)
    if 'best_params' in result:
        ab = evo.ab_test(strategy, {}, result['best_params'])
        return ab
    return result
