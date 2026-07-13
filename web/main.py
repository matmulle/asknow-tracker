import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Ticket
from db.session import AsyncSessionLocal


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(lifespan=lifespan)
_env = Environment(loader=FileSystemLoader("web/templates"), autoescape=True, cache_size=0)
templates = Jinja2Templates(env=_env)


async def get_session():
    async with AsyncSessionLocal() as session:
        yield session


REQUEST_PATTERNS = {
    "Active Directory/Windows - Group Request": "%Create New Groups%",
    "Corporate and Commercial Applications Access Request": "%Request Access for multiple corporate%",
}


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    session: AsyncSession = Depends(get_session),
    q: str = "",
    state: list[str] = Query(default=[]),
    req_type: list[str] = Query(default=[]),
    current_stage: list[str] = Query(default=[]),
    requested_by: list[str] = Query(default=[]),
    requested_for: list[str] = Query(default=[]),
):
    query = select(Ticket).order_by(Ticket.number.desc())
    if q:
        query = query.where(
            or_(
                Ticket.number.ilike(f"%{q}%"),
                Ticket.short_description.ilike(f"%{q}%"),
                Ticket.state.ilike(f"%{q}%"),
                Ticket.stage.ilike(f"%{q}%"),
                Ticket.current_stage.ilike(f"%{q}%"),
                Ticket.requested_by.ilike(f"%{q}%"),
                Ticket.groups.ilike(f"%{q}%"),
                Ticket.group_name.ilike(f"%{q}%"),
                Ticket.application.ilike(f"%{q}%"),
                Ticket.category.ilike(f"%{q}%"),
                Ticket.request_number.ilike(f"%{q}%"),
                Ticket.expected_delivery.ilike(f"%{q}%"),
                Ticket.requested_for.ilike(f"%{q}%"),
            )
        )
    if state:
        query = query.where(Ticket.state.in_(state))
    if req_type:
        query = query.where(or_(*[
            Ticket.short_description.ilike(REQUEST_PATTERNS.get(rt, f"%{rt}%"))
            for rt in req_type
        ]))
    if current_stage:
        query = query.where(Ticket.current_stage.in_(current_stage))
    if requested_by:
        query = query.where(Ticket.requested_by.in_(requested_by))
    if requested_for:
        query = query.where(Ticket.requested_for.in_(requested_for))

    result = await session.execute(query)
    tickets = result.scalars().all()

    async def distinct(col):
        r = await session.execute(select(col).distinct().where(col.isnot(None)))
        return sorted(v for (v,) in r if v)

    def to_request_label(desc):
        if not desc:
            return desc
        if "Create New Groups" in desc:
            return "Active Directory/Windows - Group Request"
        if "Request Access for multiple corporate" in desc:
            return "Corporate and Commercial Applications Access Request"
        return desc

    descs_result = await session.execute(
        select(Ticket.short_description).distinct().where(Ticket.short_description.isnot(None))
    )
    request_options = sorted({to_request_label(d) for (d,) in descs_result if d})

    return templates.TemplateResponse(
        request, "index.html",
        {
            "tickets": tickets, "q": q,
            "state": state, "states": await distinct(Ticket.state),
            "req_type": req_type, "request_options": request_options,
            "current_stage": current_stage, "current_stages": await distinct(Ticket.current_stage),
            "requested_by": requested_by, "requested_bys": await distinct(Ticket.requested_by),
            "requested_for": requested_for, "requested_fors": await distinct(Ticket.requested_for),
        },
    )


@app.get("/ticket/{number}", response_class=HTMLResponse)
async def ticket_detail(
    number: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Ticket).where(Ticket.number == number))
    ticket = result.scalar_one_or_none()
    if not ticket:
        return HTMLResponse("Ticket not found", status_code=404)
    return templates.TemplateResponse(request, "ticket.html", {"ticket": ticket})


@app.get("/sync/stream")
async def sync_stream():
    """Stream scraper output as Server-Sent Events."""
    async def generate():
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "scraper.run",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async for line in proc.stdout:
                text = line.decode("utf-8", errors="replace").rstrip()
                if text and not text.startswith("warning:") and not text.startswith("/"):
                    yield f"data: {text}\n\n"
            await proc.wait()
        except Exception as e:
            yield f"data: ERROR: {e}\n\n"
        yield "data: __done__\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
