#!/usr/bin/env python3
"""
Tối Nay Lọ Gì - Engine & Case Opening Simulator
Ported & Adapted from https://github.com/zennomi/toinaylogi
Provides:
  - CS:GO style case opening mechanism for AV actresses / JAV idols
  - Tier system with drop rates: Mythic (1%), Legendary (4%), Epic (10%), Rare (25%), Common (60%)
  - Rich curated database of top actresses with measurements, codes, photos, and tags
  - Discord Embed generators & interactive simulation
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import discord
from discord import ui

# Tier constants
TIER_COMMON = 0
TIER_RARE = 1
TIER_EPIC = 2
TIER_LEGENDARY = 3
TIER_MYTHIC = 4

TIER_DROP_RATES = {
    TIER_MYTHIC: 0.01,     # 1%
    TIER_LEGENDARY: 0.04,  # 4%
    TIER_EPIC: 0.10,       # 10%
    TIER_RARE: 0.25,       # 25%
    TIER_COMMON: 0.60,     # 60%
}

TIER_INFO = {
    TIER_MYTHIC: {
        "name": "MYTHIC · HUYỀN THOẠI",
        "short_name": "Mythic",
        "color": 0xFF1493,  # Deep Pink / Neon
        "hex": "#FF1493",
        "icon": "💎",
        "badge": "💎 MYTHIC [1%]",
        "border": "★★★★★",
    },
    TIER_LEGENDARY: {
        "name": "LEGENDARY · SIÊU CẤP",
        "short_name": "Legendary",
        "color": 0xFEE75C,  # Gold
        "hex": "#FEE75C",
        "icon": "👑",
        "badge": "👑 LEGENDARY [4%]",
        "border": "★★★★☆",
    },
    TIER_EPIC: {
        "name": "EPIC · ĐỈNH CAO",
        "short_name": "Epic",
        "color": 0x9B59B6,  # Purple
        "hex": "#9B59B6",
        "icon": "⭐",
        "badge": "⭐ EPIC [10%]",
        "border": "★★★☆☆",
    },
    TIER_RARE: {
        "name": "RARE · HIẾM",
        "short_name": "Rare",
        "color": 0x3498DB,  # Blue
        "hex": "#3498DB",
        "icon": "🔹",
        "badge": "🔹 RARE [25%]",
        "border": "★★☆☆☆",
    },
    TIER_COMMON: {
        "name": "COMMON · QUEN THUỘC",
        "short_name": "Common",
        "color": 0x95A5A6,  # Gray
        "hex": "#95A5A6",
        "icon": "⚪",
        "badge": "⚪ COMMON [60%]",
        "border": "★☆☆☆☆",
    },
}


@dataclass
class Actress:
    id: str
    name: str
    native_name: str
    tier: int
    cup: str
    height_cm: int
    measurements: str
    debut_year: int
    image_url: str
    best_code: str
    tags: list[str] = field(default_factory=list)
    quote: str = ""
    aliases: list[str] = field(default_factory=list)

    @property
    def tier_data(self) -> dict[str, Any]:
        return TIER_INFO.get(self.tier, TIER_INFO[TIER_COMMON])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "native_name": self.native_name,
            "tier": self.tier,
            "tier_name": self.tier_data["short_name"],
            "tier_badge": self.tier_data["badge"],
            "tier_color": self.tier_data["hex"],
            "cup": self.cup,
            "height_cm": self.height_cm,
            "measurements": self.measurements,
            "debut_year": self.debut_year,
            "image_url": self.image_url,
            "best_code": self.best_code,
            "tags": self.tags,
            "quote": self.quote,
        }


# Curated rich dataset of top JAV Actresses / Idols
ACTRESS_DATABASE: list[Actress] = [
    # === MYTHIC (Tier 4 - 1%) ===
    Actress(
        id="yua-mikami",
        name="Yua Mikami",
        native_name="三上悠亜",
        tier=TIER_MYTHIC,
        cup="F",
        height_cm=159,
        measurements="83-59-88",
        debut_year=2015,
        image_url="https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=500&q=80",
        best_code="SSNI-800",
        tags=["Ex-Idol", "SKE48", "Super Idol", "Legend", "Queen of JAV"],
        quote="Đệ nhất mỹ nhân ngành JAV - Nữ hoàng idol không thể xô đổ!",
    ),
    Actress(
        id="eimi-fukada",
        name="Eimi Fukada",
        native_name="深田えいみ",
        tier=TIER_MYTHIC,
        cup="F",
        height_cm=158,
        measurements="85-59-87",
        debut_year=2017,
        image_url="https://images.unsplash.com/photo-1517841905240-472988babdf9?auto=format&fit=crop&w=500&q=80",
        best_code="PRED-199",
        tags=["Sensei", "Tsun-dere", "Surgery Queen", "Cosplay", "YouTube Star"],
        quote="Cô giáo quốc dân với kỹ năng đỉnh cao khiến anh em điên đảo!",
    ),
    Actress(
        id="karen-kaede",
        name="Karen Kaede",
        native_name="楓カレン",
        tier=TIER_MYTHIC,
        cup="D",
        height_cm=162,
        measurements="82-58-86",
        debut_year=2018,
        image_url="https://images.unsplash.com/photo-1524504388940-b1c1722653e1?auto=format&fit=crop&w=500&q=80",
        best_code="IPX-512",
        tags=["Pure Beauty", "Visual Goddess", "Top Model", "Exclusive"],
        quote="Nhan sắc thiên thần thuần khiết khiến trái tim tan chảy!",
    ),
    Actress(
        id="saika-kawakita",
        name="Saika Kawakita",
        native_name="河北彩花",
        tier=TIER_MYTHIC,
        cup="E",
        height_cm=169,
        measurements="86-58-88",
        debut_year=2018,
        image_url="https://images.unsplash.com/photo-1508214751196-bcfd4ca60f91?auto=format&fit=crop&w=500&q=80",
        best_code="SSIS-062",
        tags=["Goddess Return", "Top Ranking #1", "Prestige", "Elegant"],
        quote="Nữ thần tái xuất giang hồ - Số 1 bảng xếp hạng doanh thu toàn cầu!",
    ),

    # === LEGENDARY (Tier 3 - 4%) ===
    Actress(
        id="tsukasa-aoi",
        name="Tsukasa Aoi",
        native_name="葵つかさ",
        tier=TIER_LEGENDARY,
        cup="E",
        height_cm=163,
        measurements="88-58-86",
        debut_year=2010,
        image_url="https://images.unsplash.com/photo-1529626455594-4ff0802cfb7e?auto=format&fit=crop&w=500&q=80",
        best_code="EBOD-685",
        tags=["Alice Japan", "Model", "Veteran", "Elite"],
        quote="Nét đẹp thanh tú kiêu sa cùng thân hình hoàn mỹ không góc chết!",
    ),
    Actress(
        id="minami-aizawa",
        name="Minami Aizawa",
        native_name="相沢みなみ",
        tier=TIER_LEGENDARY,
        cup="C",
        height_cm=155,
        measurements="80-54-82",
        debut_year=2016,
        image_url="https://images.unsplash.com/photo-1494790108377-be9c29b29330?auto=format&fit=crop&w=500&q=80",
        best_code="IPX-292",
        tags=["Cinderella", "DMM Grand Prix Winner", "Petite", "Emotional"],
        quote="Nàng Lọ Lem JAV - Quán quân giải DMM danh giá nhất xứ Phù Tang!",
    ),
    Actress(
        id="remu-suzumori",
        name="Remu Suzumori",
        native_name="涼森れむ",
        tier=TIER_LEGENDARY,
        cup="D",
        height_cm=160,
        measurements="84-58-86",
        debut_year=2019,
        image_url="https://images.unsplash.com/photo-1544005313-94ddf0286df2?auto=format&fit=crop&w=500&q=80",
        best_code="PRED-220",
        tags=["Translucent Skin", "Short Hair Goddess", "Natural Charm"],
        quote="Mỹ nhân tóc ngắn với làn da trắng sứ không tì vết!",
    ),
    Actress(
        id="riri-nanatsumori",
        name="Riri Nanatsumori",
        native_name="七ツ森りり",
        tier=TIER_LEGENDARY,
        cup="D",
        height_cm=154,
        measurements="86-56-85",
        debut_year=2020,
        image_url="https://images.unsplash.com/photo-1517841905240-472988babdf9?auto=format&fit=crop&w=500&q=80",
        best_code="SSIS-123",
        tags=["Gyaru Idol", "Ex-Model", "Fashionista", "Moodmaker"],
        quote="Cựu người mẫu thời trang phong cách Gyaru vạn người mê!",
    ),
    Actress(
        id="miru-sakami",
        name="Miru Sakamichi",
        native_name="坂道みる",
        tier=TIER_LEGENDARY,
        cup="C",
        height_cm=158,
        measurements="82-55-83",
        debut_year=2018,
        image_url="https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=500&q=80",
        best_code="SSNI-378",
        tags=["Super Cute", "Passionate", "S-Cute", "Top Rookie Winner"],
        quote="Nụ cười tỏa nắng và năng lượng bùng nổ trong từng thước phim!",
    ),

    # === EPIC (Tier 2 - 10%) ===
    Actress(
        id="kana-momonogi",
        name="Kana Momonogi",
        native_name="桃乃木かな",
        tier=TIER_EPIC,
        cup="F",
        height_cm=153,
        measurements="86-54-85",
        debut_year=2015,
        image_url="https://images.unsplash.com/photo-1524504388940-b1c1722653e1?auto=format&fit=crop&w=500&q=80",
        best_code="IPX-380",
        tags=["Foodie", "Ramen Lover", "Petite & Busty", "IP Star"],
        quote="Thánh ăn mì Ramen kiêm nữ thần ngọt ngào đốn tim phái mạnh!",
    ),
    Actress(
        id="hibiki-otsuki",
        name="Hibiki Otsuki",
        native_name="大槻ひびき",
        tier=TIER_EPIC,
        cup="E",
        height_cm=162,
        measurements="88-58-86",
        debut_year=2008,
        image_url="https://images.unsplash.com/photo-1508214751196-bcfd4ca60f91?auto=format&fit=crop&w=500&q=80",
        best_code="MIDE-780",
        tags=["Legendary Veteran", "Master of Skill", "Friendly", "Best Friend"],
        quote="Cây đa cây đề ngành giải trí với kỹ năng thượng thừa!",
    ),
    Actress(
        id="shoko-takahashi",
        name="Shoko Takahashi",
        native_name="高橋しょう子",
        tier=TIER_EPIC,
        cup="G",
        height_cm=161,
        measurements="87-58-88",
        debut_year=2016,
        image_url="https://images.unsplash.com/photo-1529626455594-4ff0802cfb7e?auto=format&fit=crop&w=500&q=80",
        best_code="MIDE-342",
        tags=["Gravure Queen", "Guitarist", "G-Cup Bombshell"],
        quote="Nữ hoàng Gravure một thời làm mưa làm gió khắp các bảng xếp hạng!",
    ),
    Actress(
        id="julia",
        name="Julia",
        native_name="京香JULIA",
        tier=TIER_EPIC,
        cup="J",
        height_cm=158,
        measurements="101-55-84",
        debut_year=2010,
        image_url="https://images.unsplash.com/photo-1544005313-94ddf0286df2?auto=format&fit=crop&w=500&q=80",
        best_code="JUY-880",
        tags=["J-Cup", "Milf Goddess", "Unmatched Proportions", "Classic"],
        quote="Số đo siêu thực vòng 1 chữ J đỉnh nóc kịch trần!",
    ),
    Actress(
        id="nanami-phillips",
        name="Nanami Phillips",
        native_name="七海ティナ",
        tier=TIER_EPIC,
        cup="D",
        height_cm=164,
        measurements="85-58-87",
        debut_year=2019,
        image_url="https://images.unsplash.com/photo-1494790108377-be9c29b29330?auto=format&fit=crop&w=500&q=80",
        best_code="SSIS-211",
        tags=["Half Japanese", "Exotic Look", "Tall & Slim"],
        quote="Vẻ đẹp lai đầy cuốn hút và ánh mắt hút hồn từng khung hình!",
    ),
    Actress(
        id="rion",
        name="Rion",
        native_name="安齋らら",
        tier=TIER_EPIC,
        cup="J",
        height_cm=161,
        measurements="105-58-89",
        debut_year=2013,
        image_url="https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=500&q=80",
        best_code="SSNI-643",
        tags=["God-Given Body", "S1 Supreme", "J-Cup Perfection"],
        quote="Tuyệt tác thân hình được thần linh ưu ái ban tặng!",
    ),

    # === RARE (Tier 1 - 25%) ===
    Actress(
        id="arisu-mizuhara",
        name="Arisu Mizuhara",
        native_name="水原みずき",
        tier=TIER_RARE,
        cup="D",
        height_cm=156,
        measurements="83-57-85",
        debut_year=2021,
        image_url="https://images.unsplash.com/photo-1517841905240-472988babdf9?auto=format&fit=crop&w=500&q=80",
        best_code="FSDSS-102",
        tags=["Fresh Face", "Cute Smile", "College Girl"],
        quote="Nét đẹp nữ sinh trong sáng dịu dàng như hoa anh đào đầu mùa!",
    ),
    Actress(
        id="mei-washio",
        name="Mei Washio",
        native_name="鷲尾めい",
        tier=TIER_RARE,
        cup="H",
        height_cm=165,
        measurements="92-60-89",
        debut_year=2020,
        image_url="https://images.unsplash.com/photo-1524504388940-b1c1722653e1?auto=format&fit=crop&w=500&q=80",
        best_code="FALENO-089",
        tags=["Faleno Star", "Big Breasts", "Tall & Curvy"],
        quote="Đường cong bốc lửa cùng thần thái tự tin quyến rũ chết người!",
    ),
    Actress(
        id="yui-hatano",
        name="Yui Hatano",
        native_name="波多野結衣",
        tier=TIER_RARE,
        cup="D",
        height_cm=163,
        measurements="88-59-85",
        debut_year=2008,
        image_url="https://images.unsplash.com/photo-1508214751196-bcfd4ca60f91?auto=format&fit=crop&w=500&q=80",
        best_code="MIDD-998",
        tags=["Hall of Fame", "Lin Chi-ling Lookalike", "Living Legend"],
        quote="Huyền thoại gắn liền với thanh xuân của biết bao thế hệ!",
    ),
    Actress(
        id="maria-ozawa",
        name="Maria Ozawa",
        native_name="小澤マリア",
        tier=TIER_RARE,
        cup="E",
        height_cm=162,
        measurements="88-58-86",
        debut_year=2005,
        image_url="https://images.unsplash.com/photo-1529626455594-4ff0802cfb7e?auto=format&fit=crop&w=500&q=80",
        best_code="MO-VINTAGE-01",
        tags=["Pioneer", "Nostalgia", "Global Icon", "Eurasian"],
        quote="Tượng đài bất hủ khởi đầu cho mọi giấc mơ tuổi mới lớn!",
    ),
    Actress(
        id="mana-sakura",
        name="Mana Sakura",
        native_name="紗倉まな",
        tier=TIER_RARE,
        cup="D",
        height_cm=160,
        measurements="83-58-83",
        debut_year=2011,
        image_url="https://images.unsplash.com/photo-1544005313-94ddf0286df2?auto=format&fit=crop&w=500&q=80",
        best_code="SOD-430",
        tags=["Author", "Short Hair", "SOD Princess", "Intellectual"],
        quote="Vừa là diễn viên tài năng vừa là tác giả sách bán chạy!",
    ),
    Actress(
        id="aki-sasaki",
        name="Aki Sasaki",
        native_name="佐々木あき",
        tier=TIER_RARE,
        cup="C",
        height_cm=166,
        measurements="83-57-84",
        debut_year=2015,
        image_url="https://images.unsplash.com/photo-1494790108377-be9c29b29330?auto=format&fit=crop&w=500&q=80",
        best_code="MIDE-288",
        tags=["Mature Woman", "Slender", "Elegance", "Popular Milf"],
        quote="Quý cô trưởng thành mang phong cách quý phái sang trọng!",
    ),

    # === COMMON (Tier 0 - 60%) ===
    Actress(
        id="sarah-nakamura",
        name="Sara Nakamura",
        native_name="中村サラ",
        tier=TIER_COMMON,
        cup="B",
        height_cm=152,
        measurements="78-56-80",
        debut_year=2022,
        image_url="https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=500&q=80",
        best_code="SDDE-601",
        tags=["Rookie", "Petite", "Neighborhood Girl"],
        quote="Cô em gái hàng xóm dễ thương hồn nhiên!",
    ),
    Actress(
        id="rina-ishihara",
        name="Rina Ishihara",
        native_name="石原莉奈",
        tier=TIER_COMMON,
        cup="C",
        height_cm=158,
        measurements="82-58-84",
        debut_year=2012,
        image_url="https://images.unsplash.com/photo-1517841905240-472988babdf9?auto=format&fit=crop&w=500&q=80",
        best_code="MIDE-112",
        tags=["Office Lady", "Glasses", "Classic"],
        quote="Nữ nhân viên văn phòng gương mẫu với cặp kính tri thức!",
    ),
    Actress(
        id="misa-matsuda",
        name="Misa Matsuda",
        native_name="松田美沙",
        tier=TIER_COMMON,
        cup="D",
        height_cm=161,
        measurements="85-59-86",
        debut_year=2021,
        image_url="https://images.unsplash.com/photo-1524504388940-b1c1722653e1?auto=format&fit=crop&w=500&q=80",
        best_code="SSIS-305",
        tags=["Debut", "Friendly", "Natural"],
        quote="Gương mặt quen thuộc xuất hiện đều đặn trong các tập phim!",
    ),
    Actress(
        id="yuki-koyama",
        name="Yuki Koyama",
        native_name="小山ゆき",
        tier=TIER_COMMON,
        cup="C",
        height_cm=155,
        measurements="80-57-82",
        debut_year=2023,
        image_url="https://images.unsplash.com/photo-1508214751196-bcfd4ca60f91?auto=format&fit=crop&w=500&q=80",
        best_code="IPX-890",
        tags=["New Generation", "Cute", "Casual"],
        quote="Tân binh mới gia nhập với nét duyên dáng nhẹ nhàng!",
    ),
    Actress(
        id="chika-arisawa",
        name="Chika Arisawa",
        native_name="有沢ちか",
        tier=TIER_COMMON,
        cup="E",
        height_cm=164,
        measurements="87-60-88",
        debut_year=2020,
        image_url="https://images.unsplash.com/photo-1529626455594-4ff0802cfb7e?auto=format&fit=crop&w=500&q=80",
        best_code="EBOD-801",
        tags=["Subtle", "Companionship", "Daily Life"],
        quote="Sự lựa chọn thư giãn nhẹ nhàng sau một ngày dài mệt mỏi!",
    ),
]


class ToiNayLoGiEngine:
    """
    Core engine for rolling and searching actresses.
    CS:GO Case Opening simulator logic ported from toinaylogi.
    """
    def __init__(self, database: Optional[list[Actress]] = None):
        self.db = database or ACTRESS_DATABASE
        self.stats: dict[str, int] = {
            "total_spins": 0,
            "mythic_count": 0,
            "legendary_count": 0,
            "epic_count": 0,
            "rare_count": 0,
            "common_count": 0,
        }

    def roll_tier(self) -> int:
        """Picks a tier based on exact drop rates."""
        r = random.random()
        # Cumulative:
        # Mythic: 0.00 -> 0.01
        # Legendary: 0.01 -> 0.05
        # Epic: 0.05 -> 0.15
        # Rare: 0.15 -> 0.40
        # Common: 0.40 -> 1.00
        if r < 0.01:
            return TIER_MYTHIC
        elif r < 0.05:
            return TIER_LEGENDARY
        elif r < 0.15:
            return TIER_EPIC
        elif r < 0.40:
            return TIER_RARE
        else:
            return TIER_COMMON

    def roll_actress(self, target_tier: Optional[int] = None) -> Actress:
        """Rolls a single actress from the chosen or weighted tier."""
        tier = target_tier if target_tier is not None else self.roll_tier()
        candidates = [a for a in self.db if a.tier == tier]
        if not candidates:
            candidates = self.db

        chosen = random.choice(candidates)
        self.stats["total_spins"] += 1
        if chosen.tier == TIER_MYTHIC:
            self.stats["mythic_count"] += 1
        elif chosen.tier == TIER_LEGENDARY:
            self.stats["legendary_count"] += 1
        elif chosen.tier == TIER_EPIC:
            self.stats["epic_count"] += 1
        elif chosen.tier == TIER_RARE:
            self.stats["rare_count"] += 1
        else:
            self.stats["common_count"] += 1

        return chosen

    def roll_multi(self, count: int = 10) -> list[Actress]:
        """Rolls multiple actresses (e.g. 10x Case Opening)."""
        count = max(1, min(count, 20))
        return [self.roll_actress() for _ in range(count)]

    def search(self, query: str) -> list[Actress]:
        """Search actresses by name, code, or tags."""
        q = query.strip().lower()
        if not q:
            return self.db
        results = []
        for a in self.db:
            if (
                q in a.name.lower()
                or q in a.native_name.lower()
                or q in a.best_code.lower()
                or any(q in t.lower() for t in a.tags)
            ):
                results.append(a)
        return results

    def get_by_tier(self, tier: int) -> list[Actress]:
        return [a for a in self.db if a.tier == tier]

    def generate_spin_reel(self, winning_actress: Actress, reel_length: int = 5) -> str:
        """
        Generates a horizontal CS:GO style spin reel string for visual suspense.
        E.g.: [ ⚪ Sara ] [ 🔹 Maria ] ➔ 💎 **YUA MIKAMI** 💎 ⬅ [ ⚪ Rina ] [ ⭐ Kana ]
        """
        all_others = [a for a in self.db if a.id != winning_actress.id]
        left_items = [random.choice(all_others) for _ in range(reel_length // 2)]
        right_items = [random.choice(all_others) for _ in range(reel_length // 2)]

        parts = []
        for item in left_items:
            parts.append(f"`{item.tier_data['icon']} {item.name[:9]}`")

        # Winner in center
        winner_str = f"👉 **{winning_actress.tier_data['icon']} {winning_actress.name.upper()}** 👈"
        parts.append(winner_str)

        for item in right_items:
            parts.append(f"`{item.tier_data['icon']} {item.name[:9]}`")

        return " ┃ ".join(parts)


# Global Engine Instance
default_engine = ToiNayLoGiEngine()


# ==========================================
# DISCORD UI BUILDERS
# ==========================================

def build_actress_embed(
    actress: Actress,
    user: Optional[discord.User | discord.Member] = None,
    spin_count: Optional[int] = None,
) -> discord.Embed:
    """Builds a gorgeous Discord Embed showcasing the rolled actress."""
    tdata = actress.tier_data
    color = tdata["color"]

    embed = discord.Embed(
        title=f"{tdata['icon']} {actress.name} ({actress.native_name})",
        description=f"*{actress.quote}*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        color=color,
    )

    embed.add_field(
        name="🎖️ Phẩm Cấp (Tier)",
        value=f"**{tdata['badge']}**\n{tdata['border']}",
        inline=True,
    )
    embed.add_field(
        name="📐 Số Đo 3 Vòng",
        value=f"**{actress.measurements}**\nCup: **{actress.cup}** · Cao: **{actress.height_cm}cm**",
        inline=True,
    )
    embed.add_field(
        name="🎬 Mã Phim Gợi Ý",
        value=f"[`{actress.best_code}`](https://jav.guru/?s={actress.best_code})\nNăm debut: **{actress.debut_year}**",
        inline=True,
    )

    if actress.tags:
        tags_str = " · ".join([f"`#{t}`" for t in actress.tags[:6]])
        embed.add_field(name="🏷️ Phong Cách / Thể Loại", value=tags_str, inline=False)

    # Big image or thumbnail
    if actress.image_url:
        embed.set_thumbnail(url=actress.image_url)

    footer_text = "🎰 Tối Nay Lọ Gì? · Chúc đồng chí có một buổi tối thư giãn!"
    if user:
        footer_text = f"Mở hòm bởi {user.display_name} • {footer_text}"
    embed.set_footer(text=footer_text)
    embed.timestamp = discord.utils.utcnow()

    return embed


def build_multi_embed(
    actresses: list[Actress],
    user: Optional[discord.User | discord.Member] = None,
) -> discord.Embed:
    """Builds a Discord Embed showing 10x multi-roll results."""
    # Find best pull
    best = max(actresses, key=lambda a: a.tier)
    best_tdata = best.tier_data

    embed = discord.Embed(
        title="🎰 KẾT QUẢ MỞ HÒM x10 · TỐI NAY LỌ GÌ?",
        description=(
            f"🌟 **Vật Phẩm Đỉnh Nhất:** {best_tdata['icon']} **{best.name}** ({best_tdata['badge']})\n"
            f"🎬 Mã phim gợi ý: [`{best.best_code}`](https://jav.guru/?s={best.best_code})\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=best_tdata["color"],
    )

    lines = []
    for i, a in enumerate(actresses, 1):
        td = a.tier_data
        star = "✨ " if a.tier >= TIER_EPIC else ""
        lines.append(
            f"`{i:02d}.` {td['icon']} {star}**{a.name}** — Cup **{a.cup}** | Mã: `{a.best_code}` ({td['short_name']})"
        )

    embed.add_field(name="📋 Danh Sách Rơi Được", value="\n".join(lines), inline=False)
    if best.image_url:
        embed.set_thumbnail(url=best.image_url)

    footer_text = "🎲 Dùng /toinaylogi để mở từng hòm hoặc tra cứu kho diễn viên!"
    if user:
        footer_text = f"Người mở: {user.display_name} • {footer_text}"
    embed.set_footer(text=footer_text)
    return embed


def build_tier_rates_embed() -> discord.Embed:
    """Builds an embed explaining the tiers and drop rates."""
    embed = discord.Embed(
        title="📊 TỈ LỆ RƠI HÒM · TỐI NAY LỌ GÌ?",
        description=(
            "Cơ chế mở hòm dựa theo tiêu chuẩn quay số chuẩn CS:GO Panorama.\n"
            "Mỗi khi mở hòm, hệ thống sẽ chọn ngẫu nhiên phẩm cấp theo tỉ lệ bên dưới:\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0x5865F2,
    )

    for tier, info in sorted(TIER_INFO.items(), key=lambda x: x[0], reverse=True):
        rate = TIER_DROP_RATES[tier] * 100
        count = len(default_engine.get_by_tier(tier))
        embed.add_field(
            name=f"{info['icon']} {info['name']}",
            value=(
                f"• Tỉ lệ rơi: **{rate:.1f}%**\n"
                f"• Số lượng idol trong kho: **{count}** diễn viên\n"
                f"• Độ hiếm: `{info['border']}`"
            ),
            inline=False,
        )

    embed.set_footer(text="Gõ /toinaylogi quay để thử vận may ngay hôm nay!")
    return embed


# ==========================================
# INTERACTIVE DISCORD BUTTONS VIEW
# ==========================================

class ToiNayLoGiView(ui.View):
    """Interactive view for roll buttons."""
    def __init__(self, engine: Optional[ToiNayLoGiEngine] = None, user_id: Optional[int] = None):
        super().__init__(timeout=None)
        self.engine = engine or default_engine
        self.user_id = user_id

    @ui.button(label="🎰 Mở Hòm Tiếp", style=discord.ButtonStyle.primary, emoji="🎲")
    async def btn_roll_again(self, interaction: discord.Interaction, button: ui.Button):
        actress = self.engine.roll_actress()
        reel = self.engine.generate_spin_reel(actress)
        embed = build_actress_embed(actress, interaction.user)
        is_ephemeral = bool(interaction.message and getattr(interaction.message.flags, "ephemeral", False))
        if is_ephemeral and interaction.channel:
            await interaction.response.defer()
            await interaction.channel.send(
                content=f"🌀 *{interaction.user.mention} đang quay hòm...*\n{reel}",
                embed=embed,
                view=self,
            )
        else:
            await interaction.response.edit_message(
                content=f"🌀 *Đang quay hòm...*\n{reel}",
                embed=embed,
                view=self,
            )

    @ui.button(label="🎲 Mở x10 Hòm", style=discord.ButtonStyle.success, emoji="💥")
    async def btn_roll_x10(self, interaction: discord.Interaction, button: ui.Button):
        actresses = self.engine.roll_multi(10)
        embed = build_multi_embed(actresses, interaction.user)
        is_ephemeral = bool(interaction.message and getattr(interaction.message.flags, "ephemeral", False))
        if is_ephemeral and interaction.channel:
            await interaction.response.defer()
            await interaction.channel.send(
                content=f"🎉 **{interaction.user.mention} MỞ LIỀN TAY 10 HÒM THÀNH CÔNG!**",
                embed=embed,
                view=self,
            )
        else:
            await interaction.response.edit_message(
                content="🎉 **MỞ LIỀN TAY 10 HÒM THÀNH CÔNG!**",
                embed=embed,
                view=self,
            )

    @ui.button(label="📊 Xem Tỉ Lệ", style=discord.ButtonStyle.secondary, emoji="📈")
    async def btn_show_rates(self, interaction: discord.Interaction, button: ui.Button):
        embed = build_tier_rates_embed()
        if interaction.channel:
            await interaction.response.defer()
            await interaction.channel.send(embed=embed)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=False)
