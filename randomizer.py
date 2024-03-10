import logging
import os.path
import random
import time
from datetime import datetime, timezone
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from rich import print as rp
from rich.logging import RichHandler

import cohost
from settings import POST_TO

log = logging.getLogger("randomizer")
j2env = Environment(
    loader=FileSystemLoader(""),
    autoescape=select_autoescape()
)
the_template = j2env.get_template("template.html")


def effective_tags(topmost: cohost.PostModel, share: cohost.PostModel):
    if topmost.postingProject.projectId == share.postingProject.projectId:
        return set(share.tags)  # you can always share your own post.
    return set(topmost.tags) & set(share.tags)


def main():
    yesno, which, why = cohost.am_login()
    if not yesno:
        rp("[bold bright_red]not logged in!![/]")
        exit(1)
    latest = cohost.next_id()
    if os.path.exists('last.txt'):
        with open('last.txt') as f:
            last = int(f.read().strip())
    else:
        last = 0
    with open('last.txt', 'w') as f:
        f.write(str(latest))

    rp(f'{last} -> {latest}')
    ban_list = set()
    post_info: Optional[cohost.ExtendedInfoModel] = None
    max_att = 50
    eft = list()
    verify_with = "(none?!)"
    verify_count = -1
    while 1:
        if max_att <= 0:
            log.critical("ran out of attempts or there is no more content to look at")
            exit(1)
        choiced = random.randint(last, latest)
        if choiced in ban_list:
            max_att -= 1
            continue
        ban_list.add(choiced)
        try:
            post_info = cohost.try_post(choiced)
            oc = cohost.find_the_original_content(post_info)
            describe = f'[bold bright_cyan]{post_info.post.postId}[/] by [yellow]@{post_info.post.postingProject.handle}[/]'
            if oc.post.postId != post_info.post.postId:
                # Nothing added.
                log.info(f"SKIP {describe}: [red]not additive[/]",
                         extra={"markup": True})
                continue
            if post_info.post.effectiveAdultContent:
                log.info(f"SKIP {describe}: [red]adult content[/]",
                         extra={"markup": True})
                continue
            if post_info.post.postingProject.handle in ban_list:
                log.info(f"SKIP {describe}: [red]banlist[/]",
                         extra={"markup": True})
                continue
            if not post_info.post.canShare:
                log.info(f"SKIP {describe}: [red]can't share[/]",
                         extra={"markup": True})
                continue
            if '🤖' in post_info.post.postingProject.displayName or post_info.post.postingProject.handle.endswith('-bot'):
                log.info(f"SKIP {describe}: [red]bot[/]",
                         extra={"markup": True})
                continue

            if not post_info.post.tags:
                log.info(f"SKIP {describe}: [red]published to followers only (no tags)[/]",
                         extra={"markup": True})
                continue
            if post_info.post.shareTree:
                eft = list(effective_tags(post_info.post.shareTree[0], post_info.post))
                if not eft:
                    log.info(
                        f"SKIP {describe}: [red]no effective tags[/] "
                        f"[bright_blue](no overlap between {post_info.post.shareTree[0].tags} and {post_info.post.tags})[/]",
                        extra={"markup": True})
                    continue
            else:
                eft = post_info.post.tags

            any_pass_usage_check = False
            for tag in eft:
                if len(tag) > 50:  # Probably talking in tags.
                    continue
                pass_, count = cohost.tag_analyze(tag, 3)
                if pass_:
                    any_pass_usage_check = True
                    verify_with = tag
                    verify_count = count
                    break
            if not any_pass_usage_check:
                log.info(f"SKIP {describe}: [red]no tags used by others[/] [bright_blue](of {eft})[/]",
                         extra={"markup": True})
                continue
            break
        except ValueError as e:
            log.error(e)
    assert post_info is not None

    # Share the post.
    content = the_template.render(
        original_href=post_info.post.singlePostPageUrl,

        typeof=cohost.typeof(post_info.post),
        no_eff_tags=len(eft),
        eff_tags_label=f'effective tags' if post_info.post.shareTree else 'tags',
        which_tag=f'#{verify_with}',
        tag_use_count=verify_count,

        pid=post_info.post.postId,
        handle=post_info.post.postingProject.handle,
        uid=post_info.post.postingProject.projectId,
        timestamp=datetime.now(tz=timezone.utc).strftime("%a %m %B, %Y %H:%M:%S %Z"),
        total_count=latest - last,
        percentage=f'{100.0 / (latest - last):.3f}'
    )
    cohost.switchn(POST_TO)  # required for the locking for some reason.
    pid = cohost.createShare(
        POST_TO,
        post_info.post.postId,
        [
            cohost.MarkdownBlock.of(content)
        ],
        [
            "bot",
            "randomizer/random-post"
        ]
    )
    time.sleep(0.5)
    cohost.enableShares(pid, False)
    cohost.enableComments(pid, False)
    log.info(f"SUCCESS: {pid}")


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, handlers=[RichHandler()])
    logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)
    main()
