from fastapi import APIRouter

from app.api import (
    admin,
    auth,
    chats,
    courses,
    documents,
    files,
    jobs,
    knowledge,
    language,
    market,
    planning,
    points,
    quizzes,
    reviews,
    stats,
    tangents,
    vocab,
    weaknesses,
    wiki,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(courses.router, prefix="/courses", tags=["courses"])
api_router.include_router(documents.router, tags=["documents"])
api_router.include_router(wiki.router, tags=["wiki"])
api_router.include_router(files.router, tags=["files"])
api_router.include_router(jobs.router, tags=["jobs"])
api_router.include_router(knowledge.router, tags=["knowledge"])
api_router.include_router(language.router, tags=["language"])
api_router.include_router(planning.router, tags=["planning"])
api_router.include_router(points.router, tags=["points"])
api_router.include_router(market.router, tags=["market"])
api_router.include_router(quizzes.router, tags=["quizzes"])
api_router.include_router(chats.router, tags=["chats"])
api_router.include_router(reviews.router, tags=["reviews"])
api_router.include_router(tangents.router, tags=["tangents"])
api_router.include_router(vocab.router, tags=["vocab"])
api_router.include_router(weaknesses.router, tags=["weaknesses"])
api_router.include_router(stats.router, tags=["stats"])
api_router.include_router(admin.router, tags=["admin"])
