import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "@/lib/query-client";
import { GluestackUIProvider } from "@/components/ui/gluestack-ui-provider";
import "../global.css";

export const unstable_settings = {
  initialRouteName: "(tabs)",
};

export default function RootLayout() {
  return (
    <QueryClientProvider client={queryClient}>
      <GluestackUIProvider mode="dark">
        <StatusBar style="light" backgroundColor="#0a0a0a" />
        <Stack
          screenOptions={{
            headerStyle: {
              backgroundColor: "#18181b",
            },
            headerTintColor: "#fafafa",
            headerTitleStyle: {
              fontWeight: "bold",
            },
            contentStyle: {
              backgroundColor: "#0a0a0a",
            },
          }}
        >
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
          <Stack.Screen
            name="proposal/[id]"
            options={{
              title: "Trade Proposal",
              presentation: "card",
            }}
          />
          <Stack.Screen
            name="position/[id]"
            options={{
              title: "Position Details",
              presentation: "card",
            }}
          />
          <Stack.Screen name="+not-found" options={{ title: "Oops!" }} />
        </Stack>
      </GluestackUIProvider>
    </QueryClientProvider>
  );
}
