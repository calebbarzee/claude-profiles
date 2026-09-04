import { LocalStorage } from "@raycast/api";
import { execFile } from "child_process";
import { mkdir, rm } from "fs/promises";
import { homedir } from "os";
import { join } from "path";
import { promisify } from "util";

const execFileAsync = promisify(execFile);

/** Where each isolated Claude profile's Electron `--user-data-dir` lives. */
export const PROFILES_ROOT = join(
  homedir(),
  "Library",
  "Application Support",
  "Claude Profiles",
);

const STORAGE_KEY = "claude-profiles";

export interface ClaudeProfile {
  id: string;
  name: string;
  dataDir: string;
  createdAt: number;
}

/** Turn a display name into a filesystem-safe folder slug. */
function slugify(name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "profile";
}

function uniqueSlug(name: string, existing: ClaudeProfile[]): string {
  const base = slugify(name);
  let slug = base;
  let i = 2;
  while (existing.some((p) => p.id === slug)) {
    slug = `${base}-${i++}`;
  }
  return slug;
}

export async function getProfiles(): Promise<ClaudeProfile[]> {
  const raw = await LocalStorage.getItem<string>(STORAGE_KEY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as ClaudeProfile[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

async function saveProfiles(profiles: ClaudeProfile[]): Promise<void> {
  await LocalStorage.setItem(STORAGE_KEY, JSON.stringify(profiles));
}

/** Creates a fresh, empty data directory and registers it as a profile. */
export async function addProfile(name: string): Promise<ClaudeProfile> {
  const trimmed = name.trim();
  if (!trimmed) {
    throw new Error("Profile name can't be empty");
  }
  const profiles = await getProfiles();
  const id = uniqueSlug(trimmed, profiles);
  const dataDir = join(PROFILES_ROOT, id);
  await mkdir(dataDir, { recursive: true });

  const profile: ClaudeProfile = {
    id,
    name: trimmed,
    dataDir,
    createdAt: Date.now(),
  };
  await saveProfiles([...profiles, profile]);
  return profile;
}

/** Removes a profile from the list. Optionally deletes its saved login/chat data too. */
export async function removeProfile(
  id: string,
  deleteData: boolean,
): Promise<void> {
  const profiles = await getProfiles();
  const target = profiles.find((p) => p.id === id);
  await saveProfiles(profiles.filter((p) => p.id !== id));
  if (target && deleteData) {
    await rm(target.dataDir, { recursive: true, force: true });
  }
}

/**
 * Launches a brand-new Claude Desktop instance pointed at the given data dir.
 * `-n` forces a new process even if Claude is already running under another
 * profile; `--user-data-dir` is the Electron flag that relocates all of the
 * app's persistent state (auth, chats, settings) to that folder.
 */
export async function launchClaudeProfile(dataDir: string): Promise<void> {
  await execFileAsync("open", [
    "-n",
    "-a",
    "Claude",
    "--args",
    `--user-data-dir=${dataDir}`,
  ]);
}
