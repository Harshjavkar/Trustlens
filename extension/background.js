// Opens the side panel on icon click and triggers a backend ingest every 6 hours.
const DEFAULT_URL = "http://localhost:8000";

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  chrome.alarms.create("ingest", { periodInMinutes: 360 });
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== "ingest") return;
  const { backend = DEFAULT_URL } = await chrome.storage.local.get("backend");
  try {
    await fetch(`${backend}/ingest`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  } catch (e) { /* backend offline; retry at next alarm */ }
});
