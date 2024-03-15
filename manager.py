import logging
import os.path
import re
import time

from rich.logging import RichHandler

from cohost import list_asks, post_info, delete, AskModel, ask_reject, am_login
from settings import POST_TO
from rich import print as rp

BOT_OP = [
    188410,  # @quae-nihl
    217741,  # @randomizer
]
log = logging.getLogger("manager")


def op_delete(target: int, context: AskModel):
    post = post_info(target, POST_TO)
    if post:
        immediate_author = post.post.shareTree[-1].postingProject.projectId
        root_author = post.post.shareTree[0].postingProject.projectId
        request_author = context.askingProject.projectId
        if (
            request_author == immediate_author
            or request_author == root_author
            or request_author in BOT_OP
        ):
            # Approved.
            log.info(f"DELETING {target}")
            delete(target, POST_TO)
        else:
            # Not approved.
            log.warning(
                f"access violation: "
                f"{context.askingProject.handle} ({context.askingProject.projectId}) tried to delete"
                f"share #{target}, but only {post.post.shareTree[-1].postingProject.handle} ({immediate_author}), "
                f"{post.post.shareTree[0].postingProject.handle} ({root_author}), or a bot-op can do that"
            )
    else:
        log.warning(f"{target} does not exist.")


def parse(content: str, context: AskModel):
    log.info(content)
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
        try:
            match op:
                case "delete":
                    op_delete(int(target), context)
                case _:
                    log.info("not yet implemented")
        except ValueError:
            pass
        except KeyError:
            pass
    log.info(f"discarding {context.askId} by {context.askingProject.handle}")
    ask_reject(context.askId)


def main():
    logging.basicConfig(level=logging.INFO, handlers=[RichHandler()])
    max_duration = 120  # seconds
    while os.path.exists(".lock"):
        time.sleep(1)
        max_duration -= 1
        if max_duration <= 0:
            rp("[bold bright_red]lock stuck? giving up[/]")
            exit(1)
    yesno, which, why = am_login()
    if not yesno:
        rp("[bold bright_red]not logged in!![/]")
        exit(1)
    with open(".lock", "w") as f:
        pass
    try:
        asks = list_asks(POST_TO)
        for ask in asks:
            parse(ask.content, ask)
    finally:
        os.remove(".lock")


if __name__ == "__main__":
    main()
