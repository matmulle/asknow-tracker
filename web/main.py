import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
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


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    session: AsyncSession = Depends(get_session),
    q: str = "",
    state: str = "",
    current_stage: str = "",
    group_name: str = "",
    application: str = "",
    requested_by: str = "",
    requested_for: str = "",
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
        query = query.where(Ticket.state == state)
    if current_stage:
        query = query.where(Ticket.current_stage.ilike(f"%{current_stage}%"))
    if group_name:
        query = query.where(Ticket.group_name == group_name)
    if application:
        query = query.where(Ticket.application == application)
    if requested_by:
        query = query.where(Ticket.requested_by.ilike(f"%{requested_by}%"))
    if requested_for:
        query = query.where(Ticket.requested_for.ilike(f"%{requested_for}%"))

    result = await session.execute(query)
    tickets = result.scalars().all()

    async def distinct(col):
        r = await session.execute(select(col).distinct().where(col.isnot(None)))
        return sorted(v for (v,) in r if v)

    return templates.TemplateResponse(
        request, "index.html",
        {
            "tickets": tickets, "q": q,
            "state": state, "states": await distinct(Ticket.state),
            "current_stage": current_stage, "current_stages": await distinct(Ticket.current_stage),
            "group_name": group_name, "group_names": await distinct(Ticket.group_name),
            "application": application, "applications": await distinct(Ticket.application),
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
