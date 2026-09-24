from fastapi import APIRouter

from acc.api.deps import OperatorDep, SessionFactoryDep
from acc.api.schemas import AgentDetailOut, AgentList, AgentOut
from acc.domain.errors import NotFound
from acc.ids import utcnow
from acc.queries import agents as agent_queries

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
async def list_agents(_: OperatorDep, sf: SessionFactoryDep) -> AgentList:
    agents = await agent_queries.list_agents(sf, now=utcnow())
    return AgentList(items=[AgentOut.model_validate(a) for a in agents])


@router.get("/{slug}")
async def get_agent(slug: str, _: OperatorDep, sf: SessionFactoryDep) -> AgentDetailOut:
    detail = await agent_queries.get_agent(sf, slug, now=utcnow())
    if detail is None:
        raise NotFound(f"Agent {slug} not found", agent=slug)
    return AgentDetailOut.model_validate(detail)
