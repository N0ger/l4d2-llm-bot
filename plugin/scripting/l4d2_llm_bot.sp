/**
 * L4D2 LLM Bot
 *
 * Slow (LLM) layer for survivor bots. Every few seconds the plugin serializes a
 * compact snapshot of the game state, POSTs it to a local proxy and executes the
 * returned high-level orders via VScript CommandABot (left4dhooks).
 *
 * Aiming, shooting and pathing stay with the built-in survivor bot AI. Orders are
 * "intents" with a timeout: they are re-issued periodically (CommandABot is easily
 * overridden) and reset back to normal AI on timeout, completion or any error.
 *
 * Requires: left4dhooks, RIPExt.
 */

#pragma semicolon 1
#pragma newdecls required

#include <sourcemod>
#include <sdktools>
#include <left4dhooks>
#include <ripext>

#define PLUGIN_VERSION "0.1.0"
#define PROTOCOL_VERSION 1

#define TEAM_SURVIVOR 2
#define TEAM_INFECTED 3

#define MAX_ITEMS_IN_STATE 16
#define ORDER_REACHED_ITEM 90.0
#define ORDER_REACHED_SURVIVOR 160.0
#define SAY_MAX_LEN 128

enum OrderType
{
	Order_None = 0,
	Order_Move
}

enum TargetKind
{
	Target_None = 0,
	Target_Entity,		// item or other world entity, stored as entity reference
	Target_Survivor		// survivor client, stored as userid
}

static const char g_sCharacterNames[][] =
{
	"Nick", "Rochelle", "Coach", "Ellis", "Bill", "Zoey", "Francis", "Louis"
};

static const char g_sZombieClassNames[][] =
{
	"", "smoker", "boomer", "hunter", "spitter", "jockey", "charger", "witch", "tank"
};

ConVar g_cvEnable;
ConVar g_cvUrl;
ConVar g_cvInterval;
ConVar g_cvReissue;
ConVar g_cvOrderTimeout;
ConVar g_cvHttpTimeout;
ConVar g_cvItemRadius;
ConVar g_cvInfectedRadius;
ConVar g_cvAbortRadius;
ConVar g_cvCharacters;
ConVar g_cvDebug;

Handle g_hTickTimer;
Handle g_hReissueTimer;

StringMap g_hItemKinds;		// classname (without "_spawn") -> short kind

bool g_bInFlight;
int g_iGeneration;			// bumped on map end, stale responses are dropped
int g_iSeq;

// Per-bot order state, indexed by client.
OrderType g_eOrderType[MAXPLAYERS + 1];
TargetKind g_eOrderTargetKind[MAXPLAYERS + 1];
int g_iOrderTarget[MAXPLAYERS + 1];
int g_iOrderBotUserId[MAXPLAYERS + 1];
float g_fOrderPos[MAXPLAYERS + 1][3];
float g_fOrderExpire[MAXPLAYERS + 1];
char g_sOrderLabel[MAXPLAYERS + 1][64];

public Plugin myinfo =
{
	name = "L4D2 LLM Bot",
	author = "N0ger",
	description = "High-level survivor bot decisions from an LLM via a local proxy",
	version = PLUGIN_VERSION,
	url = "https://github.com/N0ger/l4d2-llm-bot"
};

public APLRes AskPluginLoad2(Handle myself, bool late, char[] error, int err_max)
{
	if (GetEngineVersion() != Engine_Left4Dead2)
	{
		strcopy(error, err_max, "This plugin only supports Left 4 Dead 2.");
		return APLRes_SilentFailure;
	}
	return APLRes_Success;
}

public void OnPluginStart()
{
	CreateConVar("llm_bot_version", PLUGIN_VERSION, "L4D2 LLM Bot version", FCVAR_NOTIFY | FCVAR_DONTRECORD);
	g_cvEnable         = CreateConVar("llm_bot_enable", "1", "Enable the LLM decision loop.", _, true, 0.0, true, 1.0);
	g_cvUrl            = CreateConVar("llm_bot_url", "http://127.0.0.1:8787/decide", "Proxy endpoint receiving the game state.");
	g_cvInterval       = CreateConVar("llm_bot_interval", "2.0", "Seconds between state snapshots sent to the proxy.", _, true, 0.5, true, 30.0);
	g_cvReissue        = CreateConVar("llm_bot_reissue", "1.0", "Seconds between re-issuing active orders (CommandABot gets overridden).", _, true, 0.2, true, 10.0);
	g_cvOrderTimeout   = CreateConVar("llm_bot_order_timeout", "8.0", "Default and maximum order lifetime in seconds.", _, true, 1.0, true, 60.0);
	g_cvHttpTimeout    = CreateConVar("llm_bot_http_timeout", "5", "HTTP request timeout in seconds.", _, true, 1.0, true, 60.0);
	g_cvItemRadius     = CreateConVar("llm_bot_item_radius", "1500.0", "Include items within this distance of any survivor.", _, true, 100.0);
	g_cvInfectedRadius = CreateConVar("llm_bot_infected_radius", "1500.0", "Include special infected within this distance of any survivor.", _, true, 100.0);
	g_cvAbortRadius    = CreateConVar("llm_bot_abort_radius", "250.0", "Cancel a bot's move order when a special infected is this close (0 = never).", _, true, 0.0);
	g_cvCharacters     = CreateConVar("llm_bot_characters", "", "Comma separated survivor characters to control (e.g. \"Ellis,Nick\"). Empty = all survivor bots.");
	g_cvDebug          = CreateConVar("llm_bot_debug", "0", "Log requests and responses.", _, true, 0.0, true, 1.0);

	AutoExecConfig(true, "l4d2_llm_bot");

	g_cvInterval.AddChangeHook(OnTimerCvarChanged);
	g_cvReissue.AddChangeHook(OnTimerCvarChanged);
	g_cvEnable.AddChangeHook(OnEnableChanged);

	RegAdminCmd("sm_llmbot_status", Cmd_Status, ADMFLAG_GENERIC, "Show active LLM bot orders.");
	RegAdminCmd("sm_llmbot_reset", Cmd_Reset, ADMFLAG_GENERIC, "Reset all LLM bot orders back to normal AI.");

	HookEvent("round_start", Event_RoundStart, EventHookMode_PostNoCopy);

	BuildItemKinds();
	RestartTimers();
}

public void OnPluginEnd()
{
	ResetAllOrders();
}

public void OnMapEnd()
{
	g_iGeneration++;
	g_bInFlight = false;
	for (int i = 1; i <= MaxClients; i++)
	{
		ClearOrder(i);
	}
}

public void OnClientDisconnect(int client)
{
	ClearOrder(client);
}

void Event_RoundStart(Event event, const char[] name, bool dontBroadcast)
{
	g_iGeneration++;
	g_bInFlight = false;
	for (int i = 1; i <= MaxClients; i++)
	{
		ClearOrder(i);
	}
}

void OnTimerCvarChanged(ConVar convar, const char[] oldValue, const char[] newValue)
{
	RestartTimers();
}

void OnEnableChanged(ConVar convar, const char[] oldValue, const char[] newValue)
{
	if (!convar.BoolValue)
	{
		ResetAllOrders();
	}
}

void RestartTimers()
{
	delete g_hTickTimer;
	delete g_hReissueTimer;
	g_hTickTimer = CreateTimer(g_cvInterval.FloatValue, Timer_Tick, _, TIMER_REPEAT);
	g_hReissueTimer = CreateTimer(g_cvReissue.FloatValue, Timer_Reissue, _, TIMER_REPEAT);
}

void BuildItemKinds()
{
	g_hItemKinds = new StringMap();

	g_hItemKinds.SetString("weapon_first_aid_kit", "medkit");
	g_hItemKinds.SetString("weapon_pain_pills", "pills");
	g_hItemKinds.SetString("weapon_adrenaline", "adrenaline");
	g_hItemKinds.SetString("weapon_defibrillator", "defib");
	g_hItemKinds.SetString("weapon_molotov", "molotov");
	g_hItemKinds.SetString("weapon_pipe_bomb", "pipebomb");
	g_hItemKinds.SetString("weapon_vomitjar", "bile");
	g_hItemKinds.SetString("weapon_ammo", "ammo");
	g_hItemKinds.SetString("weapon_melee", "melee");
	g_hItemKinds.SetString("weapon_pistol_magnum", "magnum");

	static const char primaries[][] =
	{
		"weapon_smg", "weapon_smg_silenced", "weapon_smg_mp5",
		"weapon_pumpshotgun", "weapon_shotgun_chrome", "weapon_autoshotgun", "weapon_shotgun_spas",
		"weapon_rifle", "weapon_rifle_ak47", "weapon_rifle_desert", "weapon_rifle_sg552", "weapon_rifle_m60",
		"weapon_hunting_rifle", "weapon_sniper_military", "weapon_sniper_awp", "weapon_sniper_scout",
		"weapon_grenade_launcher"
	};
	for (int i = 0; i < sizeof(primaries); i++)
	{
		g_hItemKinds.SetString(primaries[i], "weapon");
	}
}

// ---------------------------------------------------------------------------
// Decision loop
// ---------------------------------------------------------------------------

Action Timer_Tick(Handle timer)
{
	if (!g_cvEnable.BoolValue || g_bInFlight || !IsServerProcessing())
	{
		return Plugin_Continue;
	}

	int controlled;
	JSONObject state = BuildState(controlled);
	if (controlled == 0)
	{
		delete state;
		return Plugin_Continue;
	}

	char url[256];
	g_cvUrl.GetString(url, sizeof(url));

	if (g_cvDebug.BoolValue)
	{
		char body[8192];
		state.ToString(body, sizeof(body));
		LogMessage("-> %s", body);
	}

	HTTPRequest request = new HTTPRequest(url);
	request.Timeout = g_cvHttpTimeout.IntValue;
	request.ConnectTimeout = g_cvHttpTimeout.IntValue;
	request.Post(state, OnDecisionResponse, g_iGeneration);
	delete state;

	g_bInFlight = true;
	return Plugin_Continue;
}

void OnDecisionResponse(HTTPResponse response, any generation, const char[] error)
{
	if (generation != g_iGeneration)
	{
		return; // map/round changed while the request was in flight
	}
	g_bInFlight = false;

	if (!g_cvEnable.BoolValue)
	{
		return;
	}

	if (response.Status != HTTPStatus_OK)
	{
		LogError("Proxy request failed (status %d): %s", response.Status, error);
		ResetAllOrders();
		return;
	}

	JSONObject data = view_as<JSONObject>(response.Data);
	if (data == null || !data.HasKey("orders"))
	{
		LogError("Proxy response has no \"orders\" field.");
		ResetAllOrders();
		return;
	}

	if (g_cvDebug.BoolValue)
	{
		char body[4096];
		data.ToString(body, sizeof(body));
		LogMessage("<- %s", body);
	}

	JSONArray orders = view_as<JSONArray>(data.Get("orders"));
	int count = orders.Length;
	for (int i = 0; i < count; i++)
	{
		JSONObject order = view_as<JSONObject>(orders.Get(i));
		ApplyOrder(order);
		delete order;
	}
	delete orders;
}

/**
 * Executes one order. The proxy already validates against its whitelist; this is
 * a second, independent check so nothing unexpected ever reaches the game.
 */
void ApplyOrder(JSONObject order)
{
	char botName[32], action[16];
	if (!order.GetString("bot", botName, sizeof(botName)) || !order.GetString("action", action, sizeof(action)))
	{
		return;
	}

	int bot = FindSurvivorByCharacter(botName);
	if (bot == 0 || !IsControlledBot(bot))
	{
		return;
	}

	if (StrEqual(action, "move"))
	{
		char target[64];
		if (!order.GetString("target", target, sizeof(target)))
		{
			return;
		}

		float timeout = g_cvOrderTimeout.FloatValue;
		if (order.HasKey("timeout"))
		{
			float requested = order.GetFloat("timeout");
			if (requested > 0.0 && requested < timeout)
			{
				timeout = requested;
			}
		}
		StartMoveOrder(bot, target, timeout);
	}
	else if (StrEqual(action, "reset"))
	{
		ResetOrder(bot);
	}
	else if (StrEqual(action, "say"))
	{
		char text[SAY_MAX_LEN];
		if (order.GetString("text", text, sizeof(text)))
		{
			BotSay(bot, text);
		}
	}
}

void StartMoveOrder(int bot, const char[] target, float timeout)
{
	TargetKind kind;
	int targetValue;
	float pos[3];

	int survivor = FindSurvivorByCharacter(target);
	if (survivor != 0)
	{
		if (survivor == bot || !IsPlayerAlive(survivor))
		{
			return;
		}
		kind = Target_Survivor;
		targetValue = GetClientUserId(survivor);
		GetClientAbsOrigin(survivor, pos);
	}
	else
	{
		int entity = ResolveItemId(target);
		if (entity == -1)
		{
			if (g_cvDebug.BoolValue)
			{
				LogMessage("Unknown move target \"%s\" for %N", target, bot);
			}
			return;
		}
		kind = Target_Entity;
		targetValue = EntIndexToEntRef(entity);
		GetEntPropVector(entity, Prop_Data, "m_vecAbsOrigin", pos);
	}

	g_eOrderType[bot] = Order_Move;
	g_eOrderTargetKind[bot] = kind;
	g_iOrderTarget[bot] = targetValue;
	g_iOrderBotUserId[bot] = GetClientUserId(bot);
	g_fOrderPos[bot][0] = pos[0];
	g_fOrderPos[bot][1] = pos[1];
	g_fOrderPos[bot][2] = pos[2];
	g_fOrderExpire[bot] = GetGameTime() + timeout;
	FormatEx(g_sOrderLabel[bot], sizeof(g_sOrderLabel[]), "move:%s", target);

	L4D2_CommandABot(bot, 0, BOT_CMD_MOVE, pos);
}

// ---------------------------------------------------------------------------
// Order upkeep: re-issue, completion, timeout, safety aborts
// ---------------------------------------------------------------------------

Action Timer_Reissue(Handle timer)
{
	float now = GetGameTime();

	for (int bot = 1; bot <= MaxClients; bot++)
	{
		if (g_eOrderType[bot] == Order_None)
		{
			continue;
		}

		if (!IsClientInGame(bot) || GetClientUserId(bot) != g_iOrderBotUserId[bot])
		{
			ClearOrder(bot);
			continue;
		}

		if (!IsPlayerAlive(bot) || IsIncapacitated(bot) || GetPinner(bot) != 0
			|| now >= g_fOrderExpire[bot] || IsInfectedNear(bot, g_cvAbortRadius.FloatValue))
		{
			ResetOrder(bot);
			continue;
		}

		float reach = ORDER_REACHED_ITEM;
		if (!RefreshTargetPosition(bot, reach))
		{
			ResetOrder(bot); // target picked up, killed or gone
			continue;
		}

		float origin[3];
		GetClientAbsOrigin(bot, origin);
		if (GetVectorDistance(origin, g_fOrderPos[bot]) <= reach)
		{
			ResetOrder(bot); // arrived: hand control back to the normal AI
			continue;
		}

		L4D2_CommandABot(bot, 0, BOT_CMD_MOVE, g_fOrderPos[bot]);
	}

	return Plugin_Continue;
}

bool RefreshTargetPosition(int bot, float &reach)
{
	if (g_eOrderTargetKind[bot] == Target_Survivor)
	{
		int survivor = GetClientOfUserId(g_iOrderTarget[bot]);
		if (survivor == 0 || !IsPlayerAlive(survivor))
		{
			return false;
		}
		GetClientAbsOrigin(survivor, g_fOrderPos[bot]);
		reach = ORDER_REACHED_SURVIVOR;
		return true;
	}

	int entity = EntRefToEntIndex(g_iOrderTarget[bot]);
	if (entity == INVALID_ENT_REFERENCE || IsItemOwned(entity))
	{
		return false;
	}
	GetEntPropVector(entity, Prop_Data, "m_vecAbsOrigin", g_fOrderPos[bot]);
	reach = ORDER_REACHED_ITEM;
	return true;
}

void ResetOrder(int bot)
{
	if (g_eOrderType[bot] != Order_None && IsClientInGame(bot) && IsPlayerAlive(bot))
	{
		L4D2_CommandABot(bot, 0, BOT_CMD_RESET);
	}
	ClearOrder(bot);
}

void ResetAllOrders()
{
	for (int i = 1; i <= MaxClients; i++)
	{
		ResetOrder(i);
	}
}

void ClearOrder(int bot)
{
	g_eOrderType[bot] = Order_None;
	g_eOrderTargetKind[bot] = Target_None;
	g_iOrderTarget[bot] = 0;
	g_iOrderBotUserId[bot] = 0;
	g_fOrderExpire[bot] = 0.0;
	g_sOrderLabel[bot][0] = '\0';
}

// ---------------------------------------------------------------------------
// State snapshot
// ---------------------------------------------------------------------------

JSONObject BuildState(int &controlled)
{
	controlled = 0;

	JSONObject state = new JSONObject();
	state.SetInt("v", PROTOCOL_VERSION);
	state.SetInt("seq", ++g_iSeq);
	state.SetInt("time", RoundToFloor(GetGameTime()));

	char map[64];
	GetCurrentMap(map, sizeof(map));
	state.SetString("map", map);

	float maxFlow = L4D2Direct_GetMapMaxFlowDistance();

	int survivors[MAXPLAYERS];
	int survivorCount;

	JSONArray survivorList = new JSONArray();
	for (int i = 1; i <= MaxClients; i++)
	{
		if (!IsClientInGame(i) || GetClientTeam(i) != TEAM_SURVIVOR)
		{
			continue;
		}

		bool isControlled = IsControlledBot(i);
		if (isControlled && IsPlayerAlive(i))
		{
			controlled++;
		}
		if (IsPlayerAlive(i))
		{
			survivors[survivorCount++] = i;
		}

		JSONObject s = BuildSurvivor(i, isControlled, maxFlow);
		survivorList.Push(s);
		delete s;
	}
	state.Set("survivors", survivorList);
	delete survivorList;

	JSONArray infectedList = BuildInfected(survivors, survivorCount);
	state.Set("infected", infectedList);
	delete infectedList;

	JSONArray itemList = BuildItems(survivors, survivorCount);
	state.Set("items", itemList);
	delete itemList;

	return state;
}

JSONObject BuildSurvivor(int client, bool isControlled, float maxFlow)
{
	char character[16], player[MAX_NAME_LENGTH];
	GetCharacterName(client, character, sizeof(character));
	GetClientName(client, player, sizeof(player));

	JSONObject s = new JSONObject();
	s.SetString("name", character);
	s.SetString("player", player);
	s.SetBool("bot", IsFakeClient(client));
	s.SetBool("controlled", isControlled);

	bool alive = IsPlayerAlive(client);
	s.SetBool("alive", alive);
	if (!alive)
	{
		return s;
	}

	s.SetInt("hp", GetClientHealth(client));
	s.SetInt("temp_hp", RoundToFloor(L4D_GetTempHealth(client)));
	s.SetBool("incap", IsIncapacitated(client));
	s.SetBool("ledge", GetEntProp(client, Prop_Send, "m_isHangingFromLedge") != 0);
	s.SetBool("black_white", GetEntProp(client, Prop_Send, "m_bIsOnThirdStrike") != 0);

	int pinner = GetPinner(client);
	int pinnerClass = pinner != 0 ? GetZombieClass(pinner) : 0;
	if (pinnerClass > 0 && pinnerClass < sizeof(g_sZombieClassNames))
	{
		s.SetString("pinned_by", g_sZombieClassNames[pinnerClass]);
	}
	else
	{
		s.SetString("pinned_by", "");
	}

	float pos[3];
	GetClientAbsOrigin(client, pos);
	JSONArray posArray = PositionToJson(pos);
	s.Set("pos", posArray);
	delete posArray;

	if (maxFlow > 0.0)
	{
		s.SetInt("flow", RoundToNearest(L4D2Direct_GetFlowDistance(client) / maxFlow * 100.0));
	}

	char weapon[48];
	static const char slotKeys[][] = { "primary", "secondary", "throwable", "medkit", "pills" };
	for (int slot = 0; slot < sizeof(slotKeys); slot++)
	{
		GetSlotWeapon(client, slot, weapon, sizeof(weapon));
		s.SetString(slotKeys[slot], weapon);
	}

	s.SetString("order", g_sOrderLabel[client]);
	return s;
}

JSONArray BuildInfected(const int[] survivors, int survivorCount)
{
	JSONArray list = new JSONArray();
	float radius = g_cvInfectedRadius.FloatValue;
	float pos[3];
	char id[32];

	for (int i = 1; i <= MaxClients; i++)
	{
		if (!IsClientInGame(i) || GetClientTeam(i) != TEAM_INFECTED || !IsPlayerAlive(i)
			|| GetEntProp(i, Prop_Send, "m_isGhost") != 0)
		{
			continue;
		}

		int zclass = GetZombieClass(i);
		if (zclass <= 0 || zclass >= sizeof(g_sZombieClassNames))
		{
			continue;
		}

		GetClientAbsOrigin(i, pos);
		float dist = NearestSurvivorDistance(pos, survivors, survivorCount);
		if (dist > radius)
		{
			continue;
		}

		FormatEx(id, sizeof(id), "si_%d", i);
		JSONObject entry = BuildEntityEntry(id, g_sZombieClassNames[zclass], pos, dist);
		list.Push(entry);
		delete entry;
	}

	int witch = -1;
	while ((witch = FindEntityByClassname(witch, "witch")) != -1)
	{
		GetEntPropVector(witch, Prop_Data, "m_vecAbsOrigin", pos);
		float dist = NearestSurvivorDistance(pos, survivors, survivorCount);
		if (dist > radius)
		{
			continue;
		}

		FormatEx(id, sizeof(id), "witch_%d", witch);
		JSONObject entry = BuildEntityEntry(id, "witch", pos, dist);
		list.Push(entry);
		delete entry;
	}

	return list;
}

JSONArray BuildItems(const int[] survivors, int survivorCount)
{
	float radius = g_cvItemRadius.FloatValue;
	int maxEntities = GetMaxEntities();

	// Collect candidates, then keep only the nearest MAX_ITEMS_IN_STATE.
	int candidates[MAX_ITEMS_IN_STATE];
	float distances[MAX_ITEMS_IN_STATE];
	int count;

	float pos[3];
	char kind[16];

	for (int entity = MaxClients + 1; entity < maxEntities; entity++)
	{
		if (!IsValidEdict(entity) || !GetItemKind(entity, kind, sizeof(kind)) || IsItemOwned(entity))
		{
			continue;
		}

		GetEntPropVector(entity, Prop_Data, "m_vecAbsOrigin", pos);
		float dist = NearestSurvivorDistance(pos, survivors, survivorCount);
		if (dist > radius)
		{
			continue;
		}

		if (count < MAX_ITEMS_IN_STATE)
		{
			candidates[count] = entity;
			distances[count] = dist;
			count++;
			continue;
		}

		int farthest = 0;
		for (int i = 1; i < count; i++)
		{
			if (distances[i] > distances[farthest])
			{
				farthest = i;
			}
		}
		if (dist < distances[farthest])
		{
			candidates[farthest] = entity;
			distances[farthest] = dist;
		}
	}

	JSONArray list = new JSONArray();
	char id[32], classname[64];
	for (int i = 0; i < count; i++)
	{
		int entity = candidates[i];
		GetItemKind(entity, kind, sizeof(kind));
		FormatEx(id, sizeof(id), "%s_%d", kind, entity);
		GetEntPropVector(entity, Prop_Data, "m_vecAbsOrigin", pos);

		JSONObject entry = BuildEntityEntry(id, "", pos, distances[i]);
		if (StrEqual(kind, "weapon"))
		{
			GetItemClassname(entity, classname, sizeof(classname));
			entry.SetString("class", classname[7]); // strip "weapon_"
		}
		list.Push(entry);
		delete entry;
	}
	return list;
}

JSONObject BuildEntityEntry(const char[] id, const char[] type, const float pos[3], float dist)
{
	JSONObject entry = new JSONObject();
	entry.SetString("id", id);
	if (type[0] != '\0')
	{
		entry.SetString("type", type);
	}
	JSONArray posArray = PositionToJson(pos);
	entry.Set("pos", posArray);
	delete posArray;
	entry.SetInt("dist", RoundToNearest(dist));
	return entry;
}

JSONArray PositionToJson(const float pos[3])
{
	// Whole units are plenty for the LLM and keep the payload small.
	JSONArray array = new JSONArray();
	array.PushInt(RoundToNearest(pos[0]));
	array.PushInt(RoundToNearest(pos[1]));
	array.PushInt(RoundToNearest(pos[2]));
	return array;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

bool IsControlledBot(int client)
{
	if (!IsClientInGame(client) || !IsFakeClient(client) || GetClientTeam(client) != TEAM_SURVIVOR)
	{
		return false;
	}

	// Bots standing in for an idle human are left alone.
	if (HasEntProp(client, Prop_Send, "m_humanSpectatorUserID")
		&& GetEntProp(client, Prop_Send, "m_humanSpectatorUserID") > 0)
	{
		return false;
	}

	char list[256];
	g_cvCharacters.GetString(list, sizeof(list));
	if (list[0] == '\0')
	{
		return true;
	}

	char character[16], wrappedList[260], wrappedName[20];
	GetCharacterName(client, character, sizeof(character));
	ReplaceString(list, sizeof(list), " ", "");
	FormatEx(wrappedList, sizeof(wrappedList), ",%s,", list);
	FormatEx(wrappedName, sizeof(wrappedName), ",%s,", character);
	return StrContains(wrappedList, wrappedName, false) != -1;
}

void GetCharacterName(int client, char[] buffer, int maxlength)
{
	int character = GetEntProp(client, Prop_Send, "m_survivorCharacter");
	if (character >= 0 && character < sizeof(g_sCharacterNames))
	{
		strcopy(buffer, maxlength, g_sCharacterNames[character]);
	}
	else
	{
		FormatEx(buffer, maxlength, "Survivor%d", client);
	}
}

int FindSurvivorByCharacter(const char[] name)
{
	char character[16];
	for (int i = 1; i <= MaxClients; i++)
	{
		if (!IsClientInGame(i) || GetClientTeam(i) != TEAM_SURVIVOR)
		{
			continue;
		}
		GetCharacterName(i, character, sizeof(character));
		if (StrEqual(character, name, false))
		{
			return i;
		}
	}
	return 0;
}

bool IsIncapacitated(int client)
{
	return GetEntProp(client, Prop_Send, "m_isIncapacitated") != 0;
}

int GetZombieClass(int client)
{
	return GetEntProp(client, Prop_Send, "m_zombieClass");
}

/** Returns the special infected currently pinning the survivor, or 0. */
int GetPinner(int client)
{
	static const char props[][] =
	{
		"m_tongueOwner", "m_pounceAttacker", "m_jockeyAttacker", "m_pummelAttacker", "m_carryAttacker"
	};
	for (int i = 0; i < sizeof(props); i++)
	{
		int attacker = GetEntPropEnt(client, Prop_Send, props[i]);
		if (attacker > 0 && attacker <= MaxClients && IsClientInGame(attacker))
		{
			return attacker;
		}
	}
	return 0;
}

bool IsInfectedNear(int bot, float radius)
{
	if (radius <= 0.0)
	{
		return false;
	}

	float origin[3], pos[3];
	GetClientAbsOrigin(bot, origin);
	for (int i = 1; i <= MaxClients; i++)
	{
		if (IsClientInGame(i) && GetClientTeam(i) == TEAM_INFECTED && IsPlayerAlive(i)
			&& GetEntProp(i, Prop_Send, "m_isGhost") == 0)
		{
			GetClientAbsOrigin(i, pos);
			if (GetVectorDistance(origin, pos) <= radius)
			{
				return true;
			}
		}
	}
	return false;
}

float NearestSurvivorDistance(const float pos[3], const int[] survivors, int survivorCount)
{
	float best = 999999.0;
	float origin[3];
	for (int i = 0; i < survivorCount; i++)
	{
		GetClientAbsOrigin(survivors[i], origin);
		float dist = GetVectorDistance(pos, origin);
		if (dist < best)
		{
			best = dist;
		}
	}
	return best;
}

void GetSlotWeapon(int client, int slot, char[] buffer, int maxlength)
{
	buffer[0] = '\0';
	int weapon = GetPlayerWeaponSlot(client, slot);
	if (weapon == -1)
	{
		return;
	}

	char classname[64];
	GetEdictClassname(weapon, classname, sizeof(classname));
	if (StrEqual(classname, "weapon_melee"))
	{
		// Melee weapons share one classname; the script name says which one.
		GetEntPropString(weapon, Prop_Data, "m_strMapSetScriptName", buffer, maxlength);
		return;
	}
	if (StrContains(classname, "weapon_") == 0)
	{
		strcopy(buffer, maxlength, classname[7]);
	}
	else
	{
		strcopy(buffer, maxlength, classname);
	}
}

/** Classname with any "_spawn" suffix removed. */
void GetItemClassname(int entity, char[] buffer, int maxlength)
{
	GetEdictClassname(entity, buffer, maxlength);
	int len = strlen(buffer);
	if (len > 6 && StrEqual(buffer[len - 6], "_spawn"))
	{
		buffer[len - 6] = '\0';
	}
}

bool GetItemKind(int entity, char[] kind, int maxlength)
{
	char classname[64];
	GetItemClassname(entity, classname, sizeof(classname));
	return g_hItemKinds.GetString(classname, kind, maxlength);
}

/** A weapon entity held by a player is not a pickup; *_spawn entities never are. */
bool IsItemOwned(int entity)
{
	if (!HasEntProp(entity, Prop_Send, "m_hOwnerEntity"))
	{
		return false;
	}
	return GetEntPropEnt(entity, Prop_Send, "m_hOwnerEntity") != -1;
}

/** Resolves an id like "medkit_212" back to its entity, checking the kind still matches. */
int ResolveItemId(const char[] id)
{
	int sep = FindCharInString(id, '_', true);
	if (sep <= 0)
	{
		return -1;
	}

	int entity = StringToInt(id[sep + 1]);
	if (entity <= MaxClients || !IsValidEdict(entity) || IsItemOwned(entity))
	{
		return -1;
	}

	char kind[16], expected[16];
	if (!GetItemKind(entity, kind, sizeof(kind)))
	{
		return -1;
	}
	strcopy(expected, sizeof(expected), id);
	if (sep < sizeof(expected))
	{
		expected[sep] = '\0';
	}
	return StrEqual(kind, expected) ? entity : -1;
}

void BotSay(int bot, const char[] text)
{
	// Printed directly, never routed through the console/command parser.
	char clean[SAY_MAX_LEN];
	strcopy(clean, sizeof(clean), text);
	for (int i = 0; clean[i] != '\0'; i++)
	{
		if (clean[i] < ' ')
		{
			clean[i] = ' ';
		}
	}
	TrimString(clean);
	if (clean[0] == '\0')
	{
		return;
	}
	PrintToChatAll("\x04%N\x01 :  %s", bot, clean);
}

// ---------------------------------------------------------------------------
// Admin commands
// ---------------------------------------------------------------------------

Action Cmd_Status(int client, int args)
{
	ReplyToCommand(client, "[LLM Bot] enabled=%d in_flight=%d seq=%d", g_cvEnable.BoolValue, g_bInFlight, g_iSeq);
	float now = GetGameTime();
	char character[16];
	for (int i = 1; i <= MaxClients; i++)
	{
		if (!IsControlledBot(i))
		{
			continue;
		}
		GetCharacterName(i, character, sizeof(character));
		if (g_eOrderType[i] == Order_None)
		{
			ReplyToCommand(client, "  %s: (normal AI)", character);
		}
		else
		{
			ReplyToCommand(client, "  %s: %s (%.1fs left)", character, g_sOrderLabel[i], g_fOrderExpire[i] - now);
		}
	}
	return Plugin_Handled;
}

Action Cmd_Reset(int client, int args)
{
	ResetAllOrders();
	ReplyToCommand(client, "[LLM Bot] All orders reset.");
	return Plugin_Handled;
}
