import { Platform } from "react-native";
import Constants from "expo-constants";

/**
 * Swing Trader VCP API Configuration.
 * Defaults to Android emulator host IP (10.0.2.2:8000) on Android,
 * or localhost:8000 on iOS / Web, overridable via EXPO_PUBLIC_API_URL.
 */
function getBaseUrl(): string {
  if (process.env.EXPO_PUBLIC_API_URL) {
    return process.env.EXPO_PUBLIC_API_URL;
  }

  // If running on Expo Go or dev client with hostUri
  const debuggerHost = Constants.expoConfig?.hostUri;
  if (debuggerHost) {
    const ip = debuggerHost.split(":")[0];
    return `http://${ip}:8000`;
  }

  if (Platform.OS === "android") {
    return "http://10.0.2.2:8000";
  }

  return "http://localhost:8000";
}

export const API_BASE_URL = getBaseUrl();

export const APP_CONFIG = {
  appName: "Swing Trader VCP",
  version: "1.0.0",
  apiBaseUrl: API_BASE_URL,
  marketTimezone: "Asia/Kolkata",
  defaultProductType: "CNC",
  refreshIntervals: {
    proposals: 15_000,
    positions: 5_000,
    scanner: 60_000,
    systemControls: 10_000,
  },
};
