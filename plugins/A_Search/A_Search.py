import re

from plugins import Plugins, plugin_main
from src.event_handler.GroupMessageEventHandler import GroupMessageEvent
from src.PrintLog import Log
from utils.CQHelper import CQHelper
from utils.CQType import Forward

from .src import search_api

log = Log()


class A_Search(Plugins):
    def __init__(self, server_address, bot):
        super().__init__(server_address, bot)
        self.name = "A_Search"
        self.type = "Group"
        self.author = "cojitaZ"
        self.introduction = """
                                图片搜索,支持soutubot.moe/saucenao/iqdb/google
                                usage: /search 回复需要搜索的图片
                            """

        self.SABI = search_api(enable_google=True)
        self.init_status()

    @plugin_main(call_word=["[CQ:reply,"], require_db=True)
    async def main(self, event: GroupMessageEvent, debug):
        try:
            pattern = r"id=(-?\d+).*?\]/search"
            match = re.search(pattern, event.message)
            if match:
                msg_id = match.group(1)
                msg_str = (
                    self.api.messageService.get_msg(message_id=msg_id).get("data").get("message")
                )
                self.SABI.img_url = self.get_image_filename_from_msg(msg_str)

                if self.SABI.img_url is None:
                    self.api.groupService.send_group_msg(
                        group_id=event.group_id, message="未选择图片"
                    )
                else:
                    await self.SABI.search()
                    forward = Forward()

                    forward.add_node(type="text", msg="选择的图片为")
                    forward.add_node(type="image", file_path=self.SABI.img_url)

                    for engine in self.SABI.engines:
                        forward.add_node(
                            type="text",
                            msg=(
                                f"来自{engine.name}的结果\n最大相似度为{engine.max_similarity}\n{engine.Introduction}"
                            ),
                        )
                        results = engine.result_list
                        if len(results) == 0:
                            forward.add_node(type="text", msg=engine.special_intro)
                        else:
                            temp_forward = Forward()
                            for result in results:
                                temp_forward.add_node(
                                    type="text",
                                    msg=(
                                        f"标题:{result['title']}\n"
                                        + f"相似度:{result['similarity']}\n"
                                        + f"来源:{result['origin_url']}"
                                    ),
                                )

                            forward.add_node(type="msg", msg=temp_forward.message)
                    await self.api.asyncService.send_group_forward_msg(
                        group_id=event.group_id, forward_message=forward.message
                    )

        except Exception as e:
            self.api.groupService.send_group_msg(group_id=event.group_id, message=f"{e}")
            log.error(f"{e}")
        else:
            log.debug("成功返回结果")

    def get_image_filename_from_msg(self, msg: str) -> str | None:
        result = CQHelper.load_cq(msg)
        if result is not None:
            return self.api.messageService.get_image(file_name=result.file).get("data").get("file")
        return None
