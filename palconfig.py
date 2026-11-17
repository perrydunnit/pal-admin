"""Palworld server settings: the catalogue, the file format, and the rules.

The game reads `Pal/Saved/Config/LinuxServer/PalWorldSettings.ini`, which is a
single `OptionSettings=(Key=Value,...)` line. There is no escape syntax, so a
double quote or a line break inside a quoted value corrupts every setting after
it; nothing is written until every proposed value has been validated here.

Defaults come from the game's own `DefaultPalWorldSettings.ini` (1.0.5). Keys
the game adds in a later build still show up: they are read from the file and
listed as raw values rather than silently dropped.
"""

import math
import re

# Ordered so the UI renders sections in a sensible reading order.
GROUPS = (
    "Server",
    "Difficulty & progression",
    "Combat",
    "Survival & rates",
    "Items & drops",
    "Buildings & bases",
    "Guilds & players",
    "World & events",
    "Network & API",
    "Chat & social",
    "Maintenance",
    "Other",
)

# Verbatim from the game's DefaultPalWorldSettings.ini, split only for width.
DEFAULT_OPTIONS = (
    "OptionSettings=(Difficulty=None,RandomizerType=None,RandomizerSeed=\"\","
    "bIsRandomizerPalLevelRandom=False,DayTimeSpeedRate=1.000000,NightTimeSpeedRate=1.000000,"
    "ExpRate=1.000000,PalCaptureRate=1.000000,PalSpawnNumRate=1.000000,"
    "PalDamageRateAttack=1.000000,PalDamageRateDefense=1.000000,PlayerDamageRateAttack=1.000000,"
    "PlayerDamageRateDefense=1.000000,PlayerStomachDecreaceRate=1.000000,"
    "PlayerStaminaDecreaceRate=1.000000,PlayerAutoHPRegeneRate=1.000000,"
    "PlayerAutoHpRegeneRateInSleep=1.000000,PalStomachDecreaceRate=1.000000,"
    "PalStaminaDecreaceRate=1.000000,PalAutoHPRegeneRate=1.000000,"
    "PalAutoHpRegeneRateInSleep=1.000000,BuildObjectHpRate=1.000000,"
    "BuildObjectDamageRate=1.000000,BuildObjectDeteriorationDamageRate=1.000000,"
    "CollectionDropRate=1.000000,CollectionObjectHpRate=1.000000,"
    "CollectionObjectRespawnSpeedRate=1.000000,EnemyDropItemRate=1.000000,DeathPenalty=Item,"
    "bEnablePlayerToPlayerDamage=False,bEnableFriendlyFire=False,bEnableInvaderEnemy=True,"
    "bActiveUNKO=False,bEnableAimAssistPad=True,bEnableAimAssistKeyboard=False,"
    "DropItemMaxNum=3000,PhysicsActiveDropItemMaxNum=-1,DropItemMaxNum_UNKO=100,"
    "BaseCampMaxNum=128,BaseCampWorkerMaxNum=15,DropItemAliveMaxHours=1.000000,"
    "bAutoResetGuildNoOnlinePlayers=False,AutoResetGuildTimeNoOnlinePlayers=72.000000,"
    "GuildPlayerMaxNum=20,BaseCampMaxNumInGuild=4,PalEggDefaultHatchingTime=1.000000,"
    "WorkSpeedRate=1.000000,AutoSaveSpan=30.000000,bIsMultiplay=False,bIsPvP=False,"
    "bHardcore=False,bPalLost=False,bCharacterRecreateInHardcore=False,"
    "bCanPickupOtherGuildDeathPenaltyDrop=False,bEnableNonLoginPenalty=True,"
    "bEnableFastTravel=True,bEnableFastTravelOnlyBaseCamp=False,"
    "bIsStartLocationSelectByMap=False,bExistPlayerAfterLogout=False,"
    "bEnableDefenseOtherGuildPlayer=False,bInvisibleOtherGuildBaseCampAreaFX=False,"
    "bBuildAreaLimit=False,ItemWeightRate=1.000000,CoopPlayerMaxNum=4,ServerPlayerMaxNum=32,"
    "ServerName=\"Default Palworld Server\",ServerDescription=\"\",AdminPassword=\"\","
    "ServerPassword=\"\",bAllowClientMod=True,PublicPort=8211,PublicIP=\"\",RCONEnabled=False,"
    "RCONPort=25575,Region=\"\",bUseAuth=True,"
    "BanListURL=\"https://b.palworldgame.com/api/banlist.txt\",RESTAPIEnabled=False,"
    "RESTAPIPort=8212,bShowPlayerList=False,ChatPostLimitPerMinute=30,"
    "CrossplayPlatforms=(Steam,Xbox,PS5,Mac),bIsUseBackupSaveData=True,LogFormatType=Text,"
    "bIsShowJoinLeftMessage=True,SupplyDropSpan=180,EnablePredatorBossPal=True,"
    "MaxBuildingLimitNum=0,MaxBuildingLimitNumPerPlayer=0,"
    "ServerReplicatePawnCullDistance=15000.000000,bAllowGlobalPalboxExport=True,"
    "bAllowGlobalPalboxImport=False,EquipmentDurabilityDamageRate=1.000000,"
    "ItemContainerForceMarkDirtyInterval=1.000000,"
    "PlayerDataPalStorageUpdateCheckTickInterval=1.000000,ItemCorruptionMultiplier=1.000000,"
    "MonsterFarmActionSpeedRate=1.000000,FishingDifficultyRate=1.000000,DenyTechnologyList=,"
    "GuildRejoinCooldownMinutes=0,AutoTransferMasterCheckIntervalSeconds=3600.000000,"
    "AutoTransferMasterThresholdDays=14,MaxGuildsPerFrame=10,BlockRespawnTime=5.000000,"
    "RespawnPenaltyDurationThreshold=0.000000,RespawnPenaltyTimeScale=2.000000,"
    "bDisplayPvPItemNumOnWorldMap_BaseCamp=False,bDisplayPvPItemNumOnWorldMap_Player=False,"
    "AdditionalDropItemWhenPlayerKillingInPvPMode=\"PlayerDropItem\","
    "AdditionalDropItemNumWhenPlayerKillingInPvPMode=1,"
    "bAdditionalDropItemWhenPlayerKillingInPvPMode=False,bEnableVoiceChat=False,"
    "VoiceChatMaxVolumeDistance=3000.000000,VoiceChatZeroVolumeDistance=15000.000000,"
    "bAllowEnhanceStat_Health=True,bAllowEnhanceStat_Attack=True,bAllowEnhanceStat_Stamina=True,"
    "bAllowEnhanceStat_Weight=True,bAllowEnhanceStat_WorkSpeed=True,"
    "bEnableBuildingPlayerUIdDisplay=False,BuildingNameDisplayCacheTTLSeconds=60,"
    "bAllowEnemyCampSpawnNearBaseCamp=False)"
)

HEADER = "[/Script/Pal.PalGameWorldSettings]"

# (key, group, kind, description) plus optional flags:
#   choices  - the accepted values of an enum or list
#   locked   - shown but not editable from the dashboard (see LOCKED_REASON)
#   warning  - a consequence worth reading before saving
#   secret   - value is never sent to the browser
#   tuple    - lists render as (a,b) instead of "a,b"
ENTRIES = [
    # ------------------------------------------------------------------ server
    ("ServerName", "Server", "string",
     "Name shown in the server browser and the community list."),
    ("ServerDescription", "Server", "string",
     "Free text under the server name in the browser."),
    ("ServerPassword", "Server", "string",
     "Password players must type to join. Empty means the server is open.",
     {"secret": True}),
    ("AdminPassword", "Server", "string",
     "Password for the REST API, RCON and in-game admin commands. This dashboard "
     "authenticates with it, so it is changed from the stack .env, not here.",
     {"secret": True, "locked": True}),
    ("Region", "Server", "string",
     "Region tag the game advertises; free text, mostly cosmetic."),
    ("bUseAuth", "Server", "bool",
     "Require a Steam/Xbox/PSN account to join. Turning it off drops the ban list "
     "and account identity as well.",
     {"warning": "Without authentication every join looks like a new anonymous player."}),
    ("BanListURL", "Server", "string",
     "URL the game downloads its global ban list from."),
    ("bIsMultiplay", "Server", "bool",
     "Marks the world as a multiplayer world rather than a migrated co-op save."),
    ("bShowPlayerList", "Server", "bool",
     "Let clients see the player list in-game (this dashboard is unaffected)."),
    ("bAllowClientMod", "Server", "bool",
     "Allow clients running mods to connect."),
    ("CrossplayPlatforms", "Server", "list",
     "Platforms allowed to connect. Consoles rely on the community listing, so "
     "removing Xbox or PS5 hides the server from them.",
     {"choices": ("Steam", "Xbox", "PS5", "Mac"), "tuple": True}),

    # ---------------------------------------------- difficulty & progression
    ("Difficulty", "Difficulty & progression", "enum",
     "Preset the game applies in place of unrelated combat and survival rates.",
     {"choices": ("None", "Casual", "Normal", "Hard")}),
    ("RandomizerType", "Difficulty & progression", "enum",
     "Randomizer scope: off, per region, or the whole world.",
     {"choices": ("None", "Region", "All")}),
    ("RandomizerSeed", "Difficulty & progression", "string",
     "Seed for the spawn/level randomizer. Empty picks one per world."),
    ("bIsRandomizerPalLevelRandom", "Difficulty & progression", "bool",
     "Randomize pal levels instead of using the map's level bands."),
    ("ExpRate", "Difficulty & progression", "float",
     "Experience multiplier for every player."),
    ("PalCaptureRate", "Difficulty & progression", "float",
     "Capture chance multiplier; higher makes pals easier to catch."),
    ("DeathPenalty", "Difficulty & progression", "enum",
     "What a player drops on death: nothing, items, items and equipment, or everything.",
     {"choices": ("None", "Item", "ItemAndEquipment", "All")}),
    ("bHardcore", "Difficulty & progression", "bool",
     "Hardcore mode: death is permanent for the character.",
     {"warning": "Hardcore cannot be undone for characters created under it."}),
    ("bCharacterRecreateInHardcore", "Difficulty & progression", "bool",
     "Let a hardcore player start a new character after dying."),
    ("bPalLost", "Difficulty & progression", "bool",
     "Pals are lost permanently when they die."),
    ("bEnableNonLoginPenalty", "Difficulty & progression", "bool",
     "Apply the game's inactivity penalty to players who stop logging in."),
    ("bEnableFastTravel", "Difficulty & progression", "bool",
     "Allow fast travel between unlocked points."),
    ("bEnableFastTravelOnlyBaseCamp", "Difficulty & progression", "bool",
     "Restrict fast travel to base camps."),
    ("bIsStartLocationSelectByMap", "Difficulty & progression", "bool",
     "Let new players pick their spawn point on the map."),
    ("bExistPlayerAfterLogout", "Difficulty & progression", "bool",
     "Leave the body in the world after a player logs out, so it can be attacked."),
    ("DenyTechnologyList", "Difficulty & progression", "list",
     "Technology ids players may not unlock, comma separated. Empty allows all."),
    ("FishingDifficultyRate", "Difficulty & progression", "float",
     "Difficulty multiplier for the fishing minigame."),
    ("BlockRespawnTime", "Difficulty & progression", "float",
     "Seconds before a defeated player may respawn."),
    ("RespawnPenaltyDurationThreshold", "Difficulty & progression", "float",
     "Seconds after which the respawn penalty starts scaling; 0 applies it from "
     "the first death."),
    ("RespawnPenaltyTimeScale", "Difficulty & progression", "float",
     "Multiplier applied to the respawn penalty once the threshold is passed."),

    # ------------------------------------------------------------------ combat
    ("PalDamageRateAttack", "Combat", "float",
     "Damage dealt by pals."),
    ("PalDamageRateDefense", "Combat", "float",
     "Damage pals take; lower makes them tankier."),
    ("PlayerDamageRateAttack", "Combat", "float",
     "Damage dealt by players."),
    ("PlayerDamageRateDefense", "Combat", "float",
     "Damage players take; lower makes them tankier."),
    ("bEnablePlayerToPlayerDamage", "Combat", "bool",
     "Players can damage each other, which turns on PvP encounters."),
    ("bEnableFriendlyFire", "Combat", "bool",
     "Damage applies inside a guild as well."),
    ("bEnableInvaderEnemy", "Combat", "bool",
     "Raids and invader camps spawn against players and bases."),
    ("bAllowEnemyCampSpawnNearBaseCamp", "Combat", "bool",
     "Let enemy camps spawn close to a base camp."),
    ("bEnableDefenseOtherGuildPlayer", "Combat", "bool",
     "Bases defend themselves against players from other guilds."),
    ("bInvisibleOtherGuildBaseCampAreaFX", "Combat", "bool",
     "Hide other guilds' base-area effect, which also hides where their build "
     "area starts."),
    ("bCanPickupOtherGuildDeathPenaltyDrop", "Combat", "bool",
     "Allow picking up items dropped by players of other guilds."),
    ("bIsPvP", "Combat", "bool",
     "PvP mode: player damage and PvP drops are enforced by the game."),
    ("bDisplayPvPItemNumOnWorldMap_BaseCamp", "Combat", "bool",
     "Show item counts for base camps on the world map in PvP."),
    ("bDisplayPvPItemNumOnWorldMap_Player", "Combat", "bool",
     "Show item counts for players on the world map in PvP."),
    ("AdditionalDropItemWhenPlayerKillingInPvPMode", "Combat", "string",
     "Extra item id awarded for a PvP kill."),
    ("AdditionalDropItemNumWhenPlayerKillingInPvPMode", "Combat", "int",
     "How many of that extra item are dropped."),
    ("bAdditionalDropItemWhenPlayerKillingInPvPMode", "Combat", "bool",
     "Enable the extra PvP kill drop."),
    ("bEnableAimAssistPad", "Combat", "bool",
     "Aim assist for controller players."),
    ("bEnableAimAssistKeyboard", "Combat", "bool",
     "Aim assist for mouse and keyboard players."),

    # --------------------------------------------------------- survival & rates
    ("DayTimeSpeedRate", "Survival & rates", "float",
     "Day length multiplier above 1 makes days pass faster."),
    ("NightTimeSpeedRate", "Survival & rates", "float",
     "Night length multiplier."),
    ("PlayerStomachDecreaceRate", "Survival & rates", "float",
     "How fast player hunger drains."),
    ("PlayerStaminaDecreaceRate", "Survival & rates", "float",
     "How fast player stamina drains."),
    ("PlayerAutoHPRegeneRate", "Survival & rates", "float",
     "Passive player health regeneration."),
    ("PlayerAutoHpRegeneRateInSleep", "Survival & rates", "float",
     "Player health regeneration while asleep."),
    ("PalStomachDecreaceRate", "Survival & rates", "float",
     "How fast pal hunger drains."),
    ("PalStaminaDecreaceRate", "Survival & rates", "float",
     "How fast pal stamina drains."),
    ("PalAutoHPRegeneRate", "Survival & rates", "float",
     "Passive pal health regeneration."),
    ("PalAutoHpRegeneRateInSleep", "Survival & rates", "float",
     "Pal health regeneration while asleep in the Palbox."),
    ("WorkSpeedRate", "Survival & rates", "float",
     "Base work speed for every pal."),
    ("MonsterFarmActionSpeedRate", "Survival & rates", "float",
     "Work speed for pals assigned to a ranch."),
    ("PalSpawnNumRate", "Survival & rates", "float",
     "Pal spawn density; high values cost server CPU."),
    ("PalEggDefaultHatchingTime", "Survival & rates", "float",
     "Hours a large egg takes to hatch at the default temperature."),
    ("bActiveUNKO", "Survival & rates", "bool",
     "Pals produce droppings, which can be collected as fertiliser."),
    ("bAllowEnhanceStat_Health", "Survival & rates", "bool",
     "Allow players to enhance their health stat."),
    ("bAllowEnhanceStat_Attack", "Survival & rates", "bool",
     "Allow players to enhance their attack stat."),
    ("bAllowEnhanceStat_Stamina", "Survival & rates", "bool",
     "Allow players to enhance their stamina stat."),
    ("bAllowEnhanceStat_Weight", "Survival & rates", "bool",
     "Allow players to enhance their carry weight."),
    ("bAllowEnhanceStat_WorkSpeed", "Survival & rates", "bool",
     "Allow players to enhance their work speed."),

    # ------------------------------------------------------------ items & drops
    ("ItemWeightRate", "Items & drops", "float",
     "Item weight multiplier; lower means players carry more."),
    ("EquipmentDurabilityDamageRate", "Items & drops", "float",
     "Durability loss multiplier for weapons, tools and armour."),
    ("ItemCorruptionMultiplier", "Items & drops", "float",
     "Speed at which food and other perishables spoil."),
    ("DropItemMaxNum", "Items & drops", "int",
     "Maximum dropped items the world keeps before the oldest are removed."),
    ("DropItemMaxNum_UNKO", "Items & drops", "int",
     "Maximum dropped droppings kept in the world."),
    ("PhysicsActiveDropItemMaxNum", "Items & drops", "int",
     "Dropped items with active physics; -1 means no limit."),
    ("DropItemAliveMaxHours", "Items & drops", "float",
     "Hours a dropped item survives before it despawns."),
    ("EnemyDropItemRate", "Items & drops", "float",
     "Item drop rate from defeated pals and enemies."),
    ("CollectionDropRate", "Items & drops", "float",
     "Yield from gathering nodes and pickups."),
    ("CollectionObjectHpRate", "Items & drops", "float",
     "Health of gatherable objects such as trees and ore."),
    ("CollectionObjectRespawnSpeedRate", "Items & drops", "float",
     "How quickly gathered nodes grow back."),

    # -------------------------------------------------------- buildings & bases
    ("BuildObjectHpRate", "Buildings & bases", "float",
     "Structure health multiplier."),
    ("BuildObjectDamageRate", "Buildings & bases", "float",
     "Damage structures deal to attackers."),
    ("BuildObjectDeteriorationDamageRate", "Buildings & bases", "float",
     "Decay rate of structures outside a base area."),
    ("bBuildAreaLimit", "Buildings & bases", "bool",
     "Restrict building to each base camp's own area."),
    ("BaseCampMaxNum", "Buildings & bases", "int",
     "Base camps allowed across the whole world."),
    ("BaseCampMaxNumInGuild", "Buildings & bases", "int",
     "Base camps a single guild may own."),
    ("BaseCampWorkerMaxNum", "Buildings & bases", "int",
     "Pals that can work at one base camp."),
    ("MaxBuildingLimitNum", "Buildings & bases", "int",
     "Structures allowed per base camp; 0 means unlimited."),
    ("MaxBuildingLimitNumPerPlayer", "Buildings & bases", "int",
     "Structures allowed per player; 0 means unlimited."),
    ("bEnableBuildingPlayerUIdDisplay", "Buildings & bases", "bool",
     "Show the builder's player id on structures, which helps trace griefing."),

    # -------------------------------------------------------- guilds & players
    ("ServerPlayerMaxNum", "Guilds & players", "int",
     "Players that may be online at once. The game also caps this at 32."),
    ("CoopPlayerMaxNum", "Guilds & players", "int",
     "Co-op party size."),
    ("GuildPlayerMaxNum", "Guilds & players", "int",
     "Members allowed in one guild."),
    ("GuildRejoinCooldownMinutes", "Guilds & players", "int",
     "Minutes before a player may rejoin the guild they left; 0 is immediate."),
    ("bAutoResetGuildNoOnlinePlayers", "Guilds & players", "bool",
     "Wipe guilds whose members have all been offline for the period below.",
     {"warning": "This deletes guild data, including their base camps."}),
    ("AutoResetGuildTimeNoOnlinePlayers", "Guilds & players", "float",
     "Hours of total guild inactivity before the wipe above becomes eligible."),
    ("AutoTransferMasterCheckIntervalSeconds", "Guilds & players", "float",
     "How often the game checks for inactive guild masters."),
    ("AutoTransferMasterThresholdDays", "Guilds & players", "int",
     "Days of inactivity before guild leadership passes to another member."),

    # ----------------------------------------------------------- world & events
    ("SupplyDropSpan", "World & events", "int",
     "Minutes between supply drops."),
    ("EnablePredatorBossPal", "World & events", "bool",
     "Spawn predator (alpha) boss pals in the world."),

    # ----------------------------------------------------------- network & API
    ("PublicIP", "Network & API", "string",
     "Address advertised to the community list. Empty lets the game guess."),
    ("PublicPort", "Network & API", "int",
     "Port advertised to the community list."),
    ("RESTAPIEnabled", "Network & API", "bool",
     "The REST API this dashboard polls. Turning it off blinds the dashboard and "
     "the restart button, so it is changed from the stack .env.",
     {"locked": True}),
    ("RESTAPIPort", "Network & API", "int",
     "REST API port. Changing it needs a matching PAL_API in this stack .env.",
     {"locked": True}),
    ("RCONEnabled", "Network & API", "bool",
     "RCON server for external tools; bound to loopback on this host."),
    ("RCONPort", "Network & API", "int",
     "RCON port."),
    ("bAllowGlobalPalboxExport", "Network & API", "bool",
     "Allow uploading pals to the global palbox."),
    ("bAllowGlobalPalboxImport", "Network & API", "bool",
     "Allow downloading pals from the global palbox."),
    ("LogFormatType", "Network & API", "enum",
     "Game log format. This dashboard parses the text format for joins, leaves "
     "and chat.",
     {"choices": ("Text", "Json"),
      "warning": "Json breaks presence history: the log parser only understands Text."}),

    # ------------------------------------------------------------ chat & social
    ("ChatPostLimitPerMinute", "Chat & social", "int",
     "Chat messages a player may post per minute."),
    ("bIsShowJoinLeftMessage", "Chat & social", "bool",
     "Announce joins and leaves in the in-game chat (the dashboard's history is "
     "unaffected)."),
    ("bEnableVoiceChat", "Chat & social", "bool",
     "In-game proximity voice chat."),
    ("VoiceChatMaxVolumeDistance", "Chat & social", "float",
     "Distance at which voice chat is at full volume."),
    ("VoiceChatZeroVolumeDistance", "Chat & social", "float",
     "Distance at which a voice is inaudible."),

    # ------------------------------------------------------------- maintenance
    ("AutoSaveSpan", "Maintenance", "float",
     "Seconds between autosaves."),
    ("bIsUseBackupSaveData", "Maintenance", "bool",
     "Keep the rolling world backups the game writes every ~30 s."),
    ("ServerReplicatePawnCullDistance", "Maintenance", "float",
     "Distance beyond which pawns stop being replicated to clients; raising it "
     "costs bandwidth and CPU."),
    ("MaxGuildsPerFrame", "Maintenance", "int",
     "Guilds the server processes per frame; a throttle, not a gameplay limit."),
    ("ItemContainerForceMarkDirtyInterval", "Maintenance", "float",
     "How often item containers are flushed for replication."),
    ("PlayerDataPalStorageUpdateCheckTickInterval", "Maintenance", "float",
     "How often pal storage is checked for changes."),
    ("BuildingNameDisplayCacheTTLSeconds", "Maintenance", "int",
     "How long the structure-name cache lives before it is rebuilt."),
]

LOCKED_REASON = "Managed from the stack's .env (see docs/PALWORLD-SERVER.md)."
SECRET_MASK = "••••••••"

_PAIR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)
_OPTIONS_RE = re.compile(r"OptionSettings=\((.*)\)\s*$", re.S)
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def split_pairs(payload):
    """Split `Key=Value,Key="a,b"` into pairs, ignoring commas in quotes/brackets."""
    parts, buf, quoted, depth = [], [], False, 0
    for ch in payload:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        elif ch == "," and not quoted and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    out = []
    for part in parts:
        match = _PAIR_RE.match(part.strip())
        if match:
            out.append((match.group(1), match.group(2)))
    return out


def parse(text):
    """`PalWorldSettings.ini` text -> {key: raw token}, in file order."""
    match = _OPTIONS_RE.search(text or "")
    if not match:
        return {}
    return dict(split_pairs(match.group(1)))


def render(values):
    """{key: raw token} -> a complete `PalWorldSettings.ini` body."""
    body = ",".join(f"{key}={token}" for key, token in values.items())
    return f"{HEADER}\nOptionSettings=({body})\n"


def defaults():
    return parse(DEFAULT_OPTIONS)


DEFAULTS = defaults()


def _entry(key, group, kind, description, opts=None):
    data = {"key": key, "group": group, "kind": kind, "description": description,
            "default": DEFAULTS.get(key)}
    data.update(opts or {})
    return data


def catalog(extra_keys=()):
    """Every known setting, plus any key the file carries that we do not know."""
    known = [_entry(*row[:4], row[4] if len(row) > 4 else None) for row in ENTRIES]
    seen = {entry["key"] for entry in known}
    for key in extra_keys:
        if key in seen:
            continue
        known.append({
            "key": key, "group": "Other", "kind": "raw", "default": None,
            "description": "Added by a newer game build; written back unchanged.",
        })
        seen.add(key)
    return known


def by_key(extra_keys=()):
    return {entry["key"]: entry for entry in catalog(extra_keys)}


def _unquote(token):
    token = (token or "").strip()
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    return token


def friendly(entry, token):
    """The token as a human reads it, and as the form input shows it."""
    kind = entry["kind"]
    if token is None:
        return ""
    if kind in ("bool", "enum"):
        return token.strip()
    if kind == "int":
        try:
            return str(int(float(token)))
        except ValueError:
            return token
    if kind == "float":
        try:
            value = float(token)
        except ValueError:
            return token
        return f"{value:.6f}".rstrip("0").rstrip(".") or "0"
    if kind == "list":
        items = list_items(token)
        return ",".join(items)
    if kind == "raw":
        return token
    return _unquote(token)


def list_items(token):
    token = (token or "").strip()
    if token.startswith("(") and token.endswith(")"):
        token = token[1:-1]
    else:
        token = _unquote(token)
    return [item.strip() for item in token.split(",") if item.strip()]


def _check_text(text, label):
    if '"' in text:
        raise ValueError(f"{label} cannot contain a double quote")
    if "\n" in text or "\r" in text:
        raise ValueError(f"{label} cannot contain a line break")


def coerce(entry, raw):
    """Validate one user-supplied value and return the token to write."""
    key, kind = entry["key"], entry["kind"]
    text = "" if raw is None else str(raw).strip()
    if kind in ("string", "raw"):
        if kind == "string":
            _check_text(text, key)
        return f'"{text}"' if kind == "string" else (text or '""')
    if kind == "bool":
        low = text.lower()
        if low in ("true", "1", "yes", "on"):
            return "True"
        if low in ("false", "0", "no", "off"):
            return "False"
        raise ValueError(f"{key} must be true or false")
    if kind == "int":
        try:
            return str(int(text, 10))
        except ValueError:
            raise ValueError(f"{key} must be a whole number") from None
    if kind == "float":
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"{key} must be a number") from None
        if not math.isfinite(value):
            raise ValueError(f"{key} must be a finite number")
        return f"{value:.6f}"
    if kind == "enum":
        choices = entry.get("choices") or ()
        for choice in choices:
            if choice.lower() == text.lower():
                return choice
        raise ValueError(f"{key} must be one of: {', '.join(choices)}")
    if kind == "list":
        choices = entry.get("choices") or ()
        items = [item for item in (part.strip() for part in text.split(",")) if item]
        if choices:
            allowed = {choice.lower(): choice for choice in choices}
            resolved = []
            for item in items:
                if item.lower() not in allowed:
                    raise ValueError(
                        f"{key} must be a comma-separated subset of: {', '.join(choices)}")
                resolved.append(allowed[item.lower()])
            items = resolved
        else:
            for item in items:
                _check_text(item, key)
                if not _SAFE_TOKEN_RE.match(item):
                    raise ValueError(f"{key} entries may not contain spaces or brackets")
        if entry.get("tuple"):
            if not items:
                raise ValueError(f"{key} needs at least one entry")
            return "(" + ",".join(items) + ")"
        return '"' + ",".join(items) + '"'
    raise ValueError(f"{key} has an unknown type")


def same_value(entry, left, right):
    """Compare two tokens by meaning, not spelling (`true` == `True`, 1 == 1.0)."""
    if left is None or right is None:
        return left == right
    kind = entry["kind"]
    if kind == "bool":
        return left.strip().lower() == right.strip().lower()
    if kind in ("int", "float"):
        try:
            return float(left) == float(right)
        except ValueError:
            return left.strip() == right.strip()
    if kind == "list":
        return list_items(left) == list_items(right)
    if kind == "enum":
        return left.strip().lower() == right.strip().lower()
    return _unquote(left) == _unquote(right)
