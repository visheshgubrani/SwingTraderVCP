import React from "react";
import { Switch as RNSwitch, type SwitchProps as RNSwitchProps } from "react-native";
import { clsx } from "clsx";

export interface SwitchProps extends RNSwitchProps {
  className?: string;
}

export function Switch({
  className,
  trackColor = { false: "#3f3f46", true: "#10b981" },
  thumbColor = "#fafafa",
  ...props
}: SwitchProps) {
  return (
    <RNSwitch
      trackColor={trackColor}
      thumbColor={thumbColor}
      className={clsx(className)}
      {...props}
    />
  );
}
