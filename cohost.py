from __future__ import annotations

import functools
import logging
import re
import time
from datetime import datetime
from typing import Optional, Any, Literal, Union, Protocol
from urllib.parse import urlparse, parse_qs, urlencode, quote

import requests
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field
from rich.logging import RichHandler

from settings import COHOST_COOKIE, SCRATCHPAD_HANDLE
from tryagain import parse_retry_after, backoff

try:
    from rich import print as rp
except ImportError:
    rp = print

log = logging.getLogger("cohostpy")
MAX_RETRY = 10

client = requests.Session()
client.cookies.set("connect.sid", COHOST_COOKIE)
client.headers.update({"User-Agent": "cohost-randombot operated by @quae-nihl"})


class AttachmentBlock(BaseModel):
    class Attachment(BaseModel):
        attachmentId: str
        altText: str = Field(default="")
        previewURL: str
        fileURL: str
        kind: str

    type: Literal["attachment"]
    attachment: AttachmentBlock.Attachment


class MarkdownBlock(BaseModel):
    class Content(BaseModel):
        content: str

    type: Literal["markdown"]
    markdown: MarkdownBlock.Content

    @classmethod
    def of(cls, text: str):
        return cls(type="markdown", markdown=cls.Content(content=text))


class AskBlock(BaseModel):
    class Ask(BaseModel):
        pass

    type: Literal["ask"]
    ask: AskBlock.Ask


class ProjectModel(BaseModel):
    projectId: int
    handle: str
    displayName: Optional[str]
    dek: str  # ???
    description: Optional[str]
    avatarURL: Optional[str]
    avatarPreviewURL: Optional[str]
    headerURL: Optional[str]
    headerPreviewURL: Optional[str]
    privacy: str
    url: Optional[str]
    pronouns: Optional[str]
    flags: list[Any]  # unknown!?
    avatarShape: str
    loggedOutPostVisibility: str
    frequentlyUsedTags: list[str]
    askSettings: dict
    contactCard: list
    deleteAfter: Optional[Any]
    isSelfProject: Optional[Any]


class PostModel(BaseModel):
    postId: int
    headline: str
    publishedAt: str
    filename: str
    transparentShareOfPostId: Optional[int]
    shareOfPostId: Optional[int]
    state: int  # What is this?
    numComments: int
    cws: list[str]
    tags: list[str]
    hasCohostPlus: bool
    pinned: bool
    commentsLocked: bool
    sharesLocked: bool
    blocks: list[Union[MarkdownBlock, AttachmentBlock, AskBlock]]
    plainTextBody: str
    postingProject: ProjectModel
    shareTree: list[PostModel]
    numSharedComments: int
    relatedProjects: list[ProjectModel]
    singlePostPageUrl: str
    effectiveAdultContent: bool
    isEditor: bool
    hasAnyContributorMuted: bool
    contributorBlockIncomingOrOutgoing: bool
    postEditUrl: str
    isLiked: bool
    canShare: bool
    canPublish: bool
    limitedVisibilityReason: str
    astMap: dict = Field(repr=False)  # generic. we don't need this.
    responseToAskId: Optional[int]


class CommentModel(BaseModel):
    class Comment(BaseModel):
        commentId: str
        postedAtISO: str
        deleted: bool
        body: str
        children: list[CommentModel]
        postId: int
        inReplyTo: Optional[str]
        hasCohostPlus: bool
        hidden: bool

    comment: CommentModel.Comment
    canInteract: str
    canEdit: str
    canHide: str
    poster: ProjectModel


class ExtendedInfoModel(BaseModel):
    post: PostModel
    comments: dict[str, list[CommentModel]]


def _try_with_backoff(url: str, method: str = "GET", json: Any | None = None):
    failures = 0
    while 1:
        resp = client.request(method, url, json=json)
        if resp.status_code == 418 or 500 <= resp.status_code <= 599:  # slow down!!
            log.warning(f"bad status {resp.status_code}")
            failures += 1
            if failures > MAX_RETRY:
                raise TimeoutError(f"too many retries: {failures}")
            # is there a retry-after
            if "retry-after" in resp.headers:
                new_schedule = parse_retry_after(resp.headers["retry-after"], failures)
                duration = new_schedule - datetime.now()
            else:
                # guess??
                duration = backoff(failures)
            log.info(f"waiting {duration} to cool down")
            time.sleep(duration.seconds)
            continue
        elif resp.status_code != 200:
            try:
                js = resp.json()
                if "message" in js:
                    rp(js["message"].strip())
                if "message" in js[0]["error"]:
                    rp(js[0]["error"]["message"].strip())
            except ValueError:
                pass
            except KeyError:
                pass
            raise ValueError(f"got {resp.status_code} for {url}")
        break
    return resp


def get_author_classic(pid: int):
    basic_info = _try_with_backoff(
        f"https://cohost.org/api/v1/project_post/{pid}"
    ).json()
    author = list(
        filter(
            lambda x: x["href"].startswith("/api/v1/project/")
            and not x["href"].endswith("/posts"),
            basic_info["_links"],
        )
    )
    author_name = author[0]["href"].split("/")[-1]
    return author_name


class CreatePostModel(BaseModel):
    class Content(BaseModel):
        adultContent: bool
        blocks: list[Union[AskBlock, AttachmentBlock, MarkdownBlock]]
        cws: list[str]
        headline: str
        postState: int
        tags: list[str]

    class ShareContent(Content):
        shareOfPostId: Optional[int]

    projectHandle: str
    content: Union[CreatePostModel.ShareContent, CreatePostModel.Content]


POST_INFO_TEMPLATE = (
    r"https://cohost.org/api/v1/trpc/posts.singlePost"
    r"?batch=1&input={%220%22:{%22postId%22:[[postid]],%22handle%22:%22[[handle]]%22}}"
)


class _TagAnalyzeProtocol(Protocol):
    def __call__(
        self, tag_name: str, target: int, max_pages: int = 3
    ) -> tuple[bool, str]: ...


@functools.cache  # we really shouldn't be spamming this.
def _tag_analyze(tag_name: str, target: int, max_pages: int = 10) -> tuple[bool, str]:
    """
    Count authors posting to a tag.
    :param tag_name: tag name
    :param target: target # of unique users
    :param max_pages: maximum pages to query
    :return: count
    """
    # We pull out the beautifulsoup4 for this one. please provide a tag api ;(

    reft = None
    page_count = 0
    uniques = set()
    offset = 0
    while page_count < max_pages:  # and len(uniques) < target
        url = f"https://cohost.org/rc/tagged/{quote(tag_name)}?"
        query = {}
        if offset > 0:
            query["skipPosts"] = offset
        if reft is not None:
            query["refTimestamp"] = reft
        url += urlencode(query)
        log.info(f"Check tag: {tag_name} +{offset} {url}")

        result = _try_with_backoff(url)
        soup = BeautifulSoup(result.content, "html.parser")
        # Grab the refTimestamp to avoid murdering Cohost's cache servers
        links_to_tags = soup.find_all(
            name="a", href=re.compile("^https://cohost.org/rc/tagged")
        )
        the_tag: Optional[Tag] = None
        for a in links_to_tags:
            if a.find(name="svg", recursive=False) is not None:
                the_tag = a
                break
        if the_tag is not None:
            parsed_url = urlparse(the_tag.attrs["href"])
            reft = parse_qs(parsed_url.query).get("refTimestamp", None)
        page_count += 1

        post_handle_elements = soup.select(
            "header.co-thread-header a.co-project-handle"
        )
        post_handles = list(
            map(lambda x: x.attrs["href"].split("/")[-1], post_handle_elements)
        )
        uniques.update(post_handles)
        if len(post_handles) == 0:
            return len(uniques) >= target, str(len(uniques))
        offset += len(post_handles)
    return len(uniques) >= target, f"{len(uniques)} or more"


tag_analyze: _TagAnalyzeProtocol = _tag_analyze


def get_author_hacky(pid: int):
    log.info("using share method to derive post info!")
    # Initiate a "quick-share" operation to the scratchpad account.
    model = CreatePostModel(
        projectHandle=SCRATCHPAD_HANDLE,
        content=CreatePostModel.ShareContent(
            adultContent=False,
            blocks=[],
            cws=[],
            tags=[],
            headline="",
            postState=1,
            shareOfPostId=pid,
        ),
    )
    dumped = model.model_dump(mode="json")
    resp = _try_with_backoff(
        "https://cohost.org/api/v1/trpc/posts.create?batch=1",
        method="POST",
        json={"0": dumped},
    )
    create_info = resp.json()
    known_pid = create_info[0]["result"]["data"]["postId"]
    try:
        # We now know the author.
        timeout = 3
        get_info_url = POST_INFO_TEMPLATE.replace("[[postid]]", str(known_pid)).replace(
            "[[handle]]", SCRATCHPAD_HANDLE
        )
        while 1:
            try:
                dummy_about = _try_with_backoff(get_info_url).json()
            except ValueError:
                timeout -= 1
                if timeout == 0:
                    raise ValueError(
                        "never got post info for dummy share (maybe it failed)"
                    )
                log.info("waiting...")
                time.sleep(0.5)
            else:
                break
        dummy_model = ExtendedInfoModel(**dummy_about[0]["result"]["data"])

        # Grab the original author.
        base_post_info = dummy_model.post.shareTree[-1]
        if base_post_info.postId != pid:
            log.error(f"wtf? got post id {base_post_info.postId} by sharing {pid} ???")
            raise ValueError()
        return base_post_info.postingProject.handle
    finally:
        # delete the post
        try:
            _try_with_backoff(
                "https://cohost.org/api/v1/trpc/posts.delete?batch=1",
                method="POST",
                json={"0": {"postId": known_pid, "projectHandle": SCRATCHPAD_HANDLE}},
            )
        except ValueError as e:
            log.error(f"failed to clean up: {e}")
        else:
            log.info("cleanup success")


def create_share(
    post_acct: str,
    share_of: int,
    blocks: list[Union[MarkdownBlock, AskBlock, AttachmentBlock]],
    tags: list[str],
):
    model = CreatePostModel(
        projectHandle=post_acct,
        content=CreatePostModel.ShareContent(
            adultContent=False,
            blocks=blocks,
            cws=[],
            tags=tags,
            headline="",
            postState=1,
            shareOfPostId=share_of,
        ),
    )
    dumped = model.model_dump(mode="json")
    resp = _try_with_backoff(
        "https://cohost.org/api/v1/trpc/posts.create?batch=1",
        method="POST",
        json={"0": dumped},
    )
    create_info = resp.json()
    known_pid = create_info[0]["result"]["data"]["postId"]
    return known_pid


def switch(proj_id: int):
    _try_with_backoff(
        "https://cohost.org/api/v1/trpc/projects.switchProject?batch=1",
        method="POST",
        json={"0": {"projectId": proj_id}},
    )


def switchn(proj_handle: str):
    switch(HANDLE_TO_PID[proj_handle])


def enable_shares(pid: int, enabled: bool):
    _try_with_backoff(
        "https://cohost.org/api/v1/trpc/posts.setSharesLocked?batch=1",
        method="POST",
        json={"0": {"postId": pid, "sharesLocked": not enabled}},
    )


def enable_comments(pid: int, enabled: bool):
    _try_with_backoff(
        "https://cohost.org/api/v1/trpc/posts.setCommentsLocked?batch=1",
        method="POST",
        json={"0": {"postId": pid, "commentsLocked": not enabled}},
    )


def next_id() -> int:
    model = CreatePostModel(
        projectHandle=SCRATCHPAD_HANDLE,
        content=CreatePostModel.Content(
            adultContent=False,
            blocks=[MarkdownBlock.of("don't mind me, checking next post ID")],
            cws=[],
            tags=[],
            headline="",
            postState=1,
        ),
    )
    dumped = model.model_dump(mode="json")
    resp = _try_with_backoff(
        "https://cohost.org/api/v1/trpc/posts.create?batch=1",
        method="POST",
        json={"0": dumped},
    )
    create_info = resp.json()
    known_pid = create_info[0]["result"]["data"]["postId"]
    # drop immediately
    _try_with_backoff(
        "https://cohost.org/api/v1/trpc/posts.delete?batch=1",
        method="POST",
        json={"0": {"postId": known_pid, "projectHandle": SCRATCHPAD_HANDLE}},
    )
    return known_pid


def try_post(pid: int):
    # grab the basic info
    try:
        author_name = get_author_classic(pid)
    except ValueError:
        author_name = get_author_hacky(pid)
    log.debug(f"id {pid} by {author_name}")
    custom = POST_INFO_TEMPLATE.replace("[[postid]]", str(pid)).replace(
        "[[handle]]", author_name
    )
    extinfo = _try_with_backoff(custom).json()
    # shove it into the box
    return ExtendedInfoModel(**extinfo[0]["result"]["data"])


def find_the_original_content(post: ExtendedInfoModel):
    post_model = post.post
    if post_model.transparentShareOfPostId is None:
        return post  # No change needed.

    tree = post.post.shareTree  # this isn't available on shares.
    comments = post.comments  # we need to keep these.
    index = -1
    post_model = tree[index]
    while post_model.transparentShareOfPostId is not None:
        index -= 1
        post_model = tree[index]
    modified = post_model.model_copy(update={"shareTree": tree[: index + 1]}, deep=True)
    return ExtendedInfoModel(post=modified, comments=comments)


HANDLE_TO_PID: dict[str, int] = {}


def am_login():
    query = r"https://cohost.org/api/v1/trpc/login.loggedIn,projects.listEditedProjects?batch=1&input={}"
    resp = client.get(query)
    if 400 <= resp.status_code <= 599:
        return False, [], f"bad HTTP code: {resp.status_code}"
    login, projects = resp.json()
    if not login["result"]["data"]["loggedIn"]:
        return False, [], "logged in was False"
    projects = projects["result"]["data"]["projects"]
    for project in projects:
        HANDLE_TO_PID[project["handle"]] = project["projectId"]
    project_names = list(map(lambda x: x["handle"], projects))
    return True, project_names, "success!"


def typeof(post: PostModel):
    if post.shareOfPostId is None:
        return "post"
    if post.transparentShareOfPostId is None:
        return "reply"
    if post.tags:
        return "tags"
    return "share"


def chain():
    box = try_post(4985784)
    oc = find_the_original_content(box)

    rp(
        f"last contributor: {oc.post.postId} by '{oc.post.postingProject.displayName}' "
        f"('{oc.post.postingProject.handle}')"
    )
    rp(
        f"oldest posts first for {box.post.postId}, a {typeof(box.post)} by "
        f"'{box.post.postingProject.displayName}' ('{box.post.postingProject.handle}'):"
    )

    properties = []
    if box.post.canShare:
        properties.append("can share")
    if box.post.canPublish:
        properties.append("can publish")
    if box.post.isEditor:
        properties.append("[bright_green]editor[/]")
    if box.post.commentsLocked:
        properties.append("[bright_red]comments locked[/]")
    rp(f"[bright_yellow]status: [bold]{', '.join(properties)}[/][/]")
    seen = set()
    collection = []
    for i, item in enumerate([*box.post.shareTree, box.post]):
        seen.add(item.postId)
        collection.append(item)
        rp(f"  visible: {item.postId}")
    the_post = box.post
    while the_post.shareTree and typeof(the_post.shareTree[-1]) == "share":
        shared_post_info = try_post(the_post.shareTree[-1].postId)
        if shared_post_info.post.shareTree and shared_post_info.post.shareTree[-1]:
            the_post = shared_post_info.post
            if the_post.postId not in seen:
                rp(f"  hidden : {the_post.postId}")
                seen.add(the_post.postId)
                collection.append(the_post)

    rp("full tree:")
    for item in sorted(collection, key=lambda x: x.postId):
        type_ = typeof(item).rjust(8)
        rp(
            f"  {type_}: {item.postId} \"{item.headline or '<no headline>'}\" "
            f"by '{item.postingProject.displayName}' ('{item.postingProject.handle}'): "
            f"https://cohost.org/{item.postingProject.handle}/post/{item.postId}-a"
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, handlers=[RichHandler()])
    is_logged_in, owned_projects, reason = am_login()
    if not is_logged_in:
        rp(f"[bold bright_red]Not logged in:[/] [bright_red]{reason}[/]")
    else:
        names = "[/], [bright_green]".join(map(lambda x: f"@{x}", owned_projects))
        rp(f"[bold bright_green]Logged in:[/] [bright_green]{names}[/]")

    # chain()
    # get_author_hacky(4957361)
    tag_analyze("The Cohost Global Feed", 3)
