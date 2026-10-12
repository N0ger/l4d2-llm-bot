// L4D2 Voice Commands - game-side bridge.
//
// The voice app (voice/ in the repo) writes recognized commands to
// left4dead2/ems/l4d2_voicecmd/commands.txt. This script polls that file and
// runs new commands in the listen server host's console via SendToConsole,
// e.g. "vocalize PlayerWaitHere" (Left 4 Bots 2 turns the voice line into an
// order) or "scripted_user_func l4b,coach,heal" (direct L4B2 command).
//
// File format, one entry per line:
//   S|<session id>        written once at the top, changes when the app restarts
//   <seq>|<command>       seq grows by one for every command
//
// Only whitelisted command prefixes are executed.

if (!("VoiceCmd" in getroottable()))
{
	::VoiceCmd <-
	{
		File = "l4d2_voicecmd/commands.txt"
		PollInterval = 0.1
		MaxCommandLength = 160
		AllowedPrefixes = [ "vocalize ", "scripted_user_func l4b," ]

		Session = null
		LastSeq = -1
		Primed = false
		WarnedNoHost = false
		ThinkEnt = null
	}
}

function VoiceCmd::IsAllowed(cmd)
{
	if (cmd.len() == 0 || cmd.len() > MaxCommandLength)
		return false;

	// No command chaining, quoting or line breaks.
	foreach (bad in [ ";", "\"", "\n", "\r" ])
	{
		if (cmd.find(bad) != null)
			return false;
	}

	foreach (prefix in AllowedPrefixes)
	{
		if (cmd.find(prefix) == 0)
			return true;
	}
	return false;
}

function VoiceCmd::Process(data)
{
	foreach (rawLine in split(data, "\n"))
	{
		local line = strip(rawLine);
		local sep = line.find("|");
		if (sep == null || sep == 0)
			continue;

		local head = line.slice(0, sep);
		local body = line.slice(sep + 1);

		if (head == "S")
		{
			if (body != Session)
			{
				// The app (re)started: its seq numbers start over.
				Session = body;
				LastSeq = -1;
			}
			continue;
		}

		local seq = -1;
		try { seq = head.tointeger(); } catch (e) { seq = -1; }
		if (seq <= LastSeq)
			continue;
		LastSeq = seq;

		// The first read after a map load only catches up, so commands spoken
		// during loading are not replayed in the middle of the next map.
		if (!Primed)
			continue;

		if (!IsAllowed(body))
		{
			printl("[voicecmd] rejected: " + body);
			continue;
		}

		printl("[voicecmd] > " + body);
		SendToConsole(body);
	}
	Primed = true;
}

function VoiceCmd::Poll()
{
	if (IsDedicatedServer())
	{
		if (!WarnedNoHost)
		{
			printl("[voicecmd] dedicated server - voice commands only work on a local (listen) server");
			WarnedNoHost = true;
		}
		return;
	}
	if (GetListenServerHost() == null)
		return; // host still loading in

	local data = FileToString(File);
	if (data != null)
		Process(data);
	else
		Primed = true; // nothing written yet, so nothing to replay
}

function VoiceCmd::Start()
{
	Primed = false;
	WarnedNoHost = false;

	if (ThinkEnt != null && ThinkEnt.IsValid())
		return;

	ThinkEnt = SpawnEntityFromTable("info_target", { targetname = "l4d2_voicecmd_think" });
	if (ThinkEnt == null)
	{
		printl("[voicecmd] failed to create think entity");
		return;
	}

	ThinkEnt.ValidateScriptScope();
	local scope = ThinkEnt.GetScriptScope();
	scope.VoiceCmdThink <- function()
	{
		::VoiceCmd.Poll();
		return ::VoiceCmd.PollInterval;
	}
	AddThinkToEnt(ThinkEnt, "VoiceCmdThink");
	printl("[voicecmd] listening for " + File);
}

// Round restarts (e.g. after a wipe) clean up script-spawned entities.
// Registered on every load; Start() is a no-op while the think entity lives.
::VoiceCmd.Events <-
{
	function OnGameEvent_round_start(params)
	{
		::VoiceCmd.Start();
	}
}
__CollectGameEventCallbacks(::VoiceCmd.Events);

::VoiceCmd.Start();
