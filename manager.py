import logging
import re

from cohost import list_asks, post_info, delete, AskModel
from settings import POST_TO

BOT_OP = [
    188410,  # @quae-nihl
    217741,  # @randomizer
]
log = logging.getLogger("manager")


def op_delete(target: int, context: AskModel):
    post = post_info(target, POST_TO)
    immediate_author = post.post.shareTree[-1].postingProject.projectId
    root_author = post.post.shareTree[0].postingProject.projectId
    request_author = context.askingProject.projectId
    if (
        request_author == immediate_author
        or request_author == root_author
        or request_author in BOT_OP
    ):
        # Approved.
        delete(target, POST_TO)
    else:
        # Not approved.
        log.warning(
            f"access violation: "
            f"{context.askingProject.handle} ({context.askingProject.projectId}) tried to delete"
            f"share #{target}, but only {post.post.shareTree[-1].postingProject.handle} ({immediate_author}), "
            f"{post.post.shareTree[0].postingProject.handle} ({root_author}), or a bot-op can do that"
        )


def parse(content: str, context: AskModel):
    bounds = re.search(r"```@?randomizer\r?\n([\S\s]*?)\r?\n```", content)
    if bounds is None:
        return
    instructions: list[str] = bounds.group(1).splitlines()
    for instruct in instructions:
        pattern_instruct = re.match(
            r"^(delete|suppress|unsuppress) (\d+)$", instruct.strip()
        )
        if pattern_instruct is None:
            continue  # invalid instructions (or empty lines) are ok
        op, target = pattern_instruct.groups()
        match op:
            case "delete":
                op_delete(int(target), context)
            case _:
                log.info("not yet implemented")

