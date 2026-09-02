import React from "react";
import { View, type ViewProps } from "react-native";

export interface BoxProps extends ViewProps {
  className?: string;
  children?: React.ReactNode;
}

export const Box = React.forwardRef<View, BoxProps>(function Box(
  { className, children, ...props },
  ref
) {
  return (
    <View ref={ref} className={className} {...props}>
      {children}
    </View>
  );
});

Box.displayName = "Box";
