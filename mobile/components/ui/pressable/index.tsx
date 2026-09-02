import React from "react";
import { Pressable as RNPressable, type PressableProps as RNPressableProps, View } from "react-native";

export interface PressableProps extends RNPressableProps {
  className?: string;
  children?: React.ReactNode;
}

export const Pressable = React.forwardRef<View, PressableProps>(
  function Pressable({ className, children, ...props }, ref) {
    return (
      <RNPressable ref={ref} className={className} {...props}>
        {children}
      </RNPressable>
    );
  }
);

Pressable.displayName = "Pressable";
