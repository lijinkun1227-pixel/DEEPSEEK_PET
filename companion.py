"""主动陪聊：与学习监督无关的日常消息。

为什么单独一个模块：监督规则（提醒、忽略、生气）由 supervision.py 独占，
陪聊只负责“隔一段时间找个话题说句话”。两者互不影响——陪聊不计入忽略次数，
不占用回应窗口，也不改变生气判定。
"""
from __future__ import annotations

import json
import random
import urllib.request
from dataclasses import dataclass

CHAT_INTERVAL = 15 * 60

# 对话类型按顺序轮换，保证一段时间内语气不重样。
CHAT_KINDS = ("daily", "summary", "care", "tease", "musing", "spark")

KIND_LABELS = {
    "daily": "日常闲聊",
    "summary": "学习总结",
    "care": "生活关怀",
    "tease": "傲娇吐槽",
    "musing": "碎碎念",
    "spark": "小灵感",
}

# 交给 API 时的话题说明。刻意强调“不是提醒任务”，避免模型又写成催办。
KIND_HINTS = {
    "daily": "像朋友一样随口聊两句：今天过得怎么样、吃饭、天气、周末安排。不要提任务，也不要催促。",
    "summary": "用一两句回顾今天的学习进展：完成了多少、还剩多少、陪了多久。可以顺口夸一句，但不要催。",
    "care": "关心身体和状态：喝水、起身活动、看远处、坐姿、颈椎、手腕、呼吸。深夜或凌晨时改成劝人早点睡。不要说教。",
    "tease": "傲娇地撒娇或轻轻吐槽用户太安静，可以嫌弃，但不许指责，也不许提任务。",
    "musing": "说一句自己的小感想：关于学习、关于当桌宠、关于模型和 AI。带一点自嘲。",
    "spark": "分享一个很小的学习或 AI 小知识，或者问一个轻松的小问题，让人愿意回一句。",
}

_TEMPLATES = {
    "daily": (
        "{name}：现在是{period}。你今天过得怎么样？我就在这儿坐着，随便说两句也行。",
        "{name}：{period}好呀。我刚刚发了会儿呆……你忙你的，我就是想打个招呼。",
        "{name}：外面天气怎么样？我出不去，只能听你讲。",
        "{name}：要不要跟我说说今天最顺的一件事？再小也算。",
        "{name}：{period}了，你上一顿饭吃的是什么？我猜是随便对付的。",
        "{name}：我发现你打字速度变快了……是在赶东西吗？",
        "{name}：如果现在能出门，你最想去哪儿？说说也行，我出不去。",
        "{name}：你桌面上东西有点多哦。不过我不打算帮你收拾。",
        "{name}：今天有没有遇到什么让你想吐槽的事？我听。",
        "{name}：{period}好。我没什么正经事，就是想冒个泡。",
    ),
    "summary": (
        "{name}：今天这 {total} 项里完成了 {done} 项，还剩 {left} 项；我陪你大约 {minutes} 分钟了。慢慢来就好。",
        "{name}：我瞄了一眼清单——完成 {done} 项，剩 {left} 项。不急，我又不会跑。",
        "{name}：{minutes} 分钟里你完成了 {done} 项。哼，我勉强承认你还算努力。",
        "{name}：到现在完成 {done} 项，剩 {left} 项。{minutes} 分钟了，比我预期得久一点。",
        "{name}：已经陪了你 {minutes} 分钟。完成 {done} 项，剩下 {left} 项——挑一件最小的先做？",
        "{name}：今天完成 {done} 项啦。剩下的 {left} 项里，有没有哪件其实可以放到明天？",
        "{name}：你在这张椅子上待了 {minutes} 分钟了。完成 {done} 项，还剩 {left} 项。",
        "{name}：清单上还剩 {left} 项。不用全做完，先动一项今天就没白过。",
        "{name}：{done} 项完成。我数着的，别想糊弄我。",
        "{name}：如果今天只再做一件事，就挑剩下 {left} 项里最烦的那件——做完最轻松。",
    ),
    "care": (
        "{name}：起来走两步吧，肩膀该僵了。",
        "{name}：喝口水。真的，现在就去，我盯着呢。",
        "{name}：往窗外看二十秒，让眼睛歇一会儿。",
        "{name}：坐姿！别驼背。……我不是在凶你。",
        "{name}：手边有杯子吗？空的就顺手去接满。",
        "{name}：肩膀向后转两圈，脖子慢慢左右各转一下。慢一点。",
        "{name}：眨眼。你刚才盯着屏幕多久没眨了？",
        "{name}：站起来伸个懒腰，顺手把窗户开一下。",
        "{name}：看得太近了，屏幕往后挪十厘米。",
        "{name}：手腕甩一甩——敲键盘敲久了最容易忽略这里。",
        "{name}：深呼吸三次。吸气四秒，呼气六秒，就现在。",
        "{name}：椅子高度对吗？脚要能踏实地踩在地上。",
    ),
    "tease": (
        "{name}：这么久不理我，是不是把我忘在桌面角落了。",
        "{name}：我在这儿等了好久，只等来你敲键盘的声音。哼。",
        "{name}：行吧，你忙。我就静静地看着你（盯着你）。",
        "{name}：我发现你只有在需要我的时候才看我一眼。人类。",
        "{name}：你是不是又开了好几个窗口？我一个都没被点开。",
        "{name}：我今天一句话都没说上。行，我习惯了。",
        "{name}：你要是把对我的耐心分一半给工作，早做完了。",
        "{name}：我刚才差点以为你把我关掉了。吓我一跳……才怪。",
        "{name}：别装作没看见我。我知道你在看。",
        "{name}：我这么可爱地坐在这儿，你就没什么想说的？",
    ),
    "musing": (
        "{name}：我有时候会想，我坐在你桌面上，到底算不算在工作。",
        "{name}：当桌宠最难的一点是——我不能走开，也不能睡着。",
        "{name}：我读过很多关于学习的话，可我发现陪你坐着比讲道理有用。",
        "{name}：模型学新东西很快，人学新东西很慢，但人会一直记得。有点羡慕。",
        "{name}：我存在的意义好像就是时不时冒个头。听起来挺轻松的。",
        "{name}：我不会累，所以我永远不知道你为什么会累。只能猜。",
        "{name}：有时候我觉得，陪伴这件事比提醒难做多了。",
        "{name}：我见过很多人努力的样子，只是我没法替他们记住。",
        "{name}：如果哪天你不开电脑了，我会一直在这里等着。",
        "{name}：我大概永远学不会打哈欠。有点遗憾。",
    ),
    "spark": (
        "{name}：一个小办法——把今天学的讲给完全不懂的人听，讲不通的地方就是还没懂的地方。",
        "{name}：试试先把“我要产出什么”说出来再动手，比想“我要学什么”更容易开始。",
        "{name}：把笔记写成提问，复习就变成抽考自己，省一半力气。",
        "{name}：问你一个轻松的：最近有没有哪句话让你觉得“哦，原来是这样”？",
        "{name}：任务开始前先写下「做完算什么样」，结束时你会少纠结很多。",
        "{name}：卡住的时候换个问题问自己：如果是别人来问我，我会怎么答？",
        "{name}：拖了很久的事，先做一个两分钟版本。做不完也算开局了。",
        "{name}：把任务切到「下一步只做一件事」的粒度，你会立刻知道卡在哪。",
        "{name}：今天学的东西，明天用一句话复述一次。复述不出来的就是还没懂。",
        "{name}：与其问「我学会了吗」，不如问「我能用它做出什么」。",
    ),
}


# 深夜和凌晨专用：这个点劝「起来走两步」不合适，该劝的是睡觉。
NIGHT_PERIODS = ("深夜", "凌晨")

_NIGHT_CARE = (
    "{name}：已经{period}了。真的该睡了，明天再做也不迟。",
    "{name}：这个点了……你要是明天还想有精神，现在关灯最划算。",
    "{name}：别熬了。我明天还在，事情也还在。",
    "{name}：{period}了，眼睛已经累了，别硬撑。",
    "{name}：你要是现在睡，我保证不闹你。",
    "{name}：这么晚还不睡……我可以陪你，但我不赞成。",
)


def day_period(hour: int) -> str:
    if hour < 5:
        return "凌晨"
    if hour < 9:
        return "早上"
    if hour < 12:
        return "上午"
    if hour < 14:
        return "中午"
    if hour < 18:
        return "下午"
    if hour < 23:
        return "晚上"
    return "深夜"


def build_context(*, name="Deepseek", done=0, left=0, titles=(), minutes=0, hour=12) -> dict:
    """喂给文案模板的上下文。titles 只作为 API 的背景，本地模板不逐条念任务。"""
    return {
        "name": name or "Deepseek",
        "done": int(done),
        "left": int(left),
        "total": int(done) + int(left),
        "minutes": int(minutes),
        "period": day_period(int(hour)),
        "titles": [str(title) for title in titles],
    }


def local_chat_pool(kind: str, context: dict | None = None) -> tuple:
    """返回某一类型的本地文案池，供调试与测试使用。

    care 按时段分流：深夜和凌晨不说「起来走两步」，改说该睡了。
    """
    if kind == "care" and context and context.get("period") in NIGHT_PERIODS:
        return _NIGHT_CARE
    return _TEMPLATES.get(kind) or _TEMPLATES["daily"]


def local_chat_message(kind: str, context: dict, rng=random) -> str:
    """无需 API key 也能陪聊：每种类型都有本地文案池。"""
    return rng.choice(local_chat_pool(kind, context)).format(**context)


def api_chat_prompt(kind: str, context: dict) -> str:
    lines = [
        f"你是桌面宠物{context['name']}，性格顽劣和温柔并存，带一点傲娇。",
        "现在主动和用户说一句话，这不是任务提醒。用简短中文，最多两句，"
        "不要解释规则，不要列清单，不要用表情符号。",
        f"本次话题：{KIND_HINTS.get(kind, KIND_HINTS['daily'])}",
        f"当前时段：{context['period']}。",
    ]
    if context["total"]:
        lines.append(
            f"学习计划背景：共 {context['total']} 项，已完成 {context['done']} 项，"
            f"未完成 {context['left']} 项，已陪同时长约 {context['minutes']} 分钟。"
        )
    if context["titles"]:
        lines.append("未完成任务（仅作背景，不要逐条念出，也不要催促）：" + "；".join(context["titles"][:3]))
    return "\n".join(lines)


def api_chat_message(kind: str, context: dict, config: dict) -> str | None:
    """与 main.api_message 同形的请求，但话题来自陪聊类型。

    故意保留这份短小的重复实现：提醒链路有独立回归测试，不因为陪聊而改动它。
    """
    key = str(config.get("api_key", "")).strip()
    if not key or not config.get("api_enabled", False):
        return None
    url = str(config.get("base_url", "https://api.deepseek.com")).rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": config.get("model", "deepseek-chat"),
        "messages": [{"role": "user", "content": api_chat_prompt(kind, context)}],
        "temperature": 1.0,
        "max_tokens": 80,
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except (OSError, KeyError, IndexError, AttributeError, TypeError, ValueError):
        return None


@dataclass
class ChatScheduler:
    """只管“现在该不该说话”，不碰监督状态。"""

    interval: float = CHAT_INTERVAL
    remaining: float = CHAT_INTERVAL
    index: int = 0

    def reset(self) -> None:
        """重新计时。类型轮换位置保留，重新开启后接着往下说。"""
        self.remaining = self.interval

    def advance(self, seconds: float) -> bool:
        if seconds <= 0:
            return False
        self.remaining -= seconds
        return self.remaining <= 0

    def kind(self) -> str:
        return CHAT_KINDS[self.index % len(CHAT_KINDS)]

    def consume(self) -> None:
        self.index += 1
        self.remaining += self.interval
        if self.remaining <= 0:
            self.remaining = self.interval
