import { Tabs } from "expo-router";
import {
  TrendingUp,
  Sparkles,
  Layers,
  SlidersHorizontal,
  ShieldCheck,
} from "lucide-react-native";

export default function TabLayout() {
  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: "#fafafa",
        tabBarInactiveTintColor: "#71717a",
        tabBarStyle: {
          backgroundColor: "#18181b",
          borderTopColor: "#27272a",
          height: 60,
          paddingBottom: 8,
          paddingTop: 8,
        },
        tabBarLabelStyle: {
          fontSize: 11,
          fontWeight: "600",
        },
        headerStyle: {
          backgroundColor: "#18181b",
          borderBottomColor: "#27272a",
          borderBottomWidth: 1,
        },
        headerTintColor: "#fafafa",
        headerTitleStyle: {
          fontWeight: "bold",
          fontSize: 18,
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Watchlist",
          tabBarLabel: "Watchlist",
          tabBarIcon: ({ color, size }) => (
            <TrendingUp size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="proposals"
        options={{
          title: "P10 Proposals",
          tabBarLabel: "Proposals",
          tabBarIcon: ({ color, size }) => (
            <Sparkles size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="positions"
        options={{
          title: "Positions & Risk",
          tabBarLabel: "Positions",
          tabBarIcon: ({ color, size }) => (
            <Layers size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="scanner"
        options={{
          title: "VCP Scanner",
          tabBarLabel: "Scanner",
          tabBarIcon: ({ color, size }) => (
            <SlidersHorizontal size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="settings"
        options={{
          title: "Controls & System",
          tabBarLabel: "Controls",
          tabBarIcon: ({ color, size }) => (
            <ShieldCheck size={size} color={color} />
          ),
        }}
      />
    </Tabs>
  );
}
