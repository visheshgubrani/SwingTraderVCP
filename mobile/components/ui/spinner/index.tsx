import React from "react";
import { ActivityIndicator, type ActivityIndicatorProps } from "react-native";
import { clsx } from "clsx";

export interface SpinnerProps extends ActivityIndicatorProps {
  className?: string;
}

export function Spinner({
  size = "small",
  color = "#ffffff",
  className,
  ...props
}: SpinnerProps) {
  return (
    <ActivityIndicator
      size={size}
      color={color}
      className={clsx(className)}
      {...props}
    />
  );
}
