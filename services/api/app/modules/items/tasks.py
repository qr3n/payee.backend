from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession
from taskiq import TaskiqDepends

from app.api.deps import get_db
from app.core.broker import broker
from app.core.logging import get_logger
from app.modules.items import service as item_service

logger = get_logger(__name__)


@broker.task(task_name="items:process_item_analysis")
async def process_item_analysis(
    item_id: UUID,
    db: AsyncSession = TaskiqDepends(get_db),
) -> dict[str, str]:
    """
    Background worker task demonstrating asynchronous processing
    with dependency injection. Fetches item from DB and performs analysis.
    """
    logger.info("Starting background analysis for item", item_id=str(item_id))

    item = await item_service.get_item(session=db, item_id=item_id)
    if not item:
        logger.warning("Item not found for background analysis", item_id=str(item_id))
        return {"status": "not_found", "item_id": str(item_id)}

    # Simulated async analysis or external AI / webhook work
    logger.info(
        "Item background analysis completed successfully",
        item_id=str(item_id),
        title=item.title,
    )
    return {
        "status": "completed",
        "item_id": str(item_id),
        "title": item.title,
    }
