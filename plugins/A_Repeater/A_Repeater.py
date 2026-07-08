import random
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import BigInteger, Column, DateTime, Text, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker

from plugins import Plugins, plugin_main
from src.event_handler import GroupMessageEventHandler
from src.PrintLog import Log

log = Log()

Base = declarative_base()


class Message(Base):
    __tablename__ = "messages"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    group_id = Column(BigInteger, nullable=False)
    msg = Column(Text, nullable=False)
    send_time = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    msg_id = Column(BigInteger, nullable=False, default=0)
    user_nickname = Column(Text, nullable=False, default=" ")
    user_card = Column(Text, nullable=False, default=" ")


class A_Repeater(Plugins):
    def __init__(self, server_address, bot):
        super().__init__(server_address, bot)
        self.name = "A_Repeater"
        self.type = "Group"
        self.author = "cojitaZ"
        self.introduction = """
                                就要复读!
                                usage: <auto>
                            """

        self.session_factory = sessionmaker(
            bind=self.bot.database, class_=AsyncSession, expire_on_commit=False
        )
        self.now_path = Path(__file__).resolve().parent

        self.init_status()

    @plugin_main()
    async def main(self, event: GroupMessageEventHandler, debug):
        message = event.message
        self.ban_word = ["/open", "JM", "/close", "pid", "/search"]
        try:
            if not any(word in message for word in self.ban_word):
                now_utc = datetime.now(UTC)
                ten_minutes_ago = now_utc - timedelta(minutes=60)
                async with self.session_factory() as session:
                    stmt = (
                        select(Message)
                        .where(Message.group_id == event.group_id)
                        .order_by(desc(Message.send_time))
                        .where(Message.send_time >= ten_minutes_ago)
                    )

                    result = await session.execute(stmt)
                    _random = random.randint(0, 100)
                    rows = result.scalars().all()
                    smo_repeated = 0
                    have_i_repeated = False
                    for row in reversed(rows):
                        if row.msg == message and not row.msg_id == event.message_id:
                            smo_repeated += 1
                            if row.user_id == self.bot.bot_id:
                                have_i_repeated = True
                                break
                    if re.match(r"^\[.*\]$", message):
                        _CQ = 3
                    else:
                        _CQ = 1
                    lens = len(message)
                    repeat_ = _random < ((smo_repeated * 20) * (lens / 10) / _CQ)
                    if repeat_ and not have_i_repeated:
                        self.api.groupService.send_group_msg(
                            group_id=event.group_id, message=message
                        )
                        log.debug(f"时间{datetime.now(UTC)}触发复读")
        except Exception as e:
            log.error(e)
