import React from "react";
import { View, ViewProps } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

export type ModeType = "light" | "dark" | "system";

export interface GluestackUIProviderProps extends ViewProps {
  mode?: ModeType;
  children?: React.ReactNode;
}

export function GluestackUIProvider({
  mode = "dark",
  children,
  style,
  ...props
}: GluestackUIProviderProps) {
  return (
    <SafeAreaProvider>
      <View
        style={[{ flex: 1 }, style]}
        className="flex-1 bg-background"
        {...props}
      >
        {children}
      </View>
    </SafeAreaProvider>
  );
}
