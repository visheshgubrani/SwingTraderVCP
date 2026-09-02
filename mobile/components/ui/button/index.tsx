import React, { createContext, useContext } from "react";
import {
  Pressable as RNPressable,
  Text as RNText,
  ActivityIndicator,
  type PressableProps,
  type TextProps,
  View,
} from "react-native";
import { clsx } from "clsx";

export type ButtonVariant =
  | "default"
  | "destructive"
  | "outline"
  | "secondary"
  | "ghost"
  | "link"
  | "success";

export type ButtonSize = "default" | "xs" | "sm" | "lg" | "icon";

interface ButtonContextValue {
  variant: ButtonVariant;
  size: ButtonSize;
  isDisabled?: boolean;
}

const ButtonContext = createContext<ButtonContextValue>({
  variant: "default",
  size: "default",
});

export interface ButtonProps extends PressableProps {
  variant?: ButtonVariant;
  size?: ButtonSize;
  isDisabled?: boolean;
  className?: string;
  children?: React.ReactNode;
}

const variantButtonClasses: Record<ButtonVariant, string> = {
  default: "bg-primary active:opacity-90",
  destructive: "bg-destructive active:opacity-90",
  outline: "border border-border bg-transparent active:bg-accent",
  secondary: "bg-secondary active:opacity-80",
  ghost: "bg-transparent active:bg-accent",
  link: "bg-transparent",
  success: "bg-success active:opacity-90",
};

const sizeButtonClasses: Record<ButtonSize, string> = {
  xs: "px-2.5 py-1 rounded-md",
  sm: "px-3 py-1.5 rounded-md",
  default: "px-4 py-2.5 rounded-lg",
  lg: "px-6 py-3 rounded-lg",
  icon: "h-10 w-10 p-0 rounded-lg justify-center items-center",
};

export const Button = React.forwardRef<View, ButtonProps>(function Button(
  {
    variant = "default",
    size = "default",
    isDisabled = false,
    className,
    children,
    disabled,
    ...props
  },
  ref
) {
  const isButtonDisabled = Boolean(isDisabled || disabled);

  return (
    <ButtonContext.Provider
      value={{ variant, size, isDisabled: isButtonDisabled }}
    >
      <RNPressable
        ref={ref}
        disabled={isButtonDisabled}
        className={clsx(
          "flex-row items-center justify-center gap-2",
          variantButtonClasses[variant],
          sizeButtonClasses[size],
          isButtonDisabled && "opacity-50 pointer-events-none",
          className
        )}
        {...props}
      >
        {children}
      </RNPressable>
    </ButtonContext.Provider>
  );
});

Button.displayName = "Button";

export interface ButtonTextProps extends TextProps {
  className?: string;
  children?: React.ReactNode;
}

const variantTextClasses: Record<ButtonVariant, string> = {
  default: "text-primary-foreground font-semibold",
  destructive: "text-destructive-foreground font-semibold",
  outline: "text-foreground font-medium",
  secondary: "text-secondary-foreground font-medium",
  ghost: "text-foreground font-medium",
  link: "text-primary underline font-medium",
  success: "text-success-foreground font-semibold",
};

const sizeTextClasses: Record<ButtonSize, string> = {
  xs: "text-xs",
  sm: "text-sm",
  default: "text-base",
  lg: "text-lg",
  icon: "text-sm",
};

export const ButtonText = React.forwardRef<RNText, ButtonTextProps>(
  function ButtonText({ className, children, ...props }, ref) {
    const { variant, size } = useContext(ButtonContext);

    return (
      <RNText
        ref={ref}
        className={clsx(
          variantTextClasses[variant],
          sizeTextClasses[size],
          className
        )}
        {...props}
      >
        {children}
      </RNText>
    );
  }
);

ButtonText.displayName = "ButtonText";

export interface ButtonIconProps {
  as: React.ComponentType<any>;
  size?: number;
  color?: string;
  className?: string;
}

export function ButtonIcon({
  as: IconComponent,
  size,
  color,
  className,
}: ButtonIconProps) {
  const { size: buttonSize } = useContext(ButtonContext);
  const iconSize = size ?? (buttonSize === "xs" ? 14 : buttonSize === "sm" ? 16 : 18);

  return <IconComponent size={iconSize} color={color} className={className} />;
}

export interface ButtonSpinnerProps {
  color?: string;
  size?: "small" | "large";
  className?: string;
}

export function ButtonSpinner({
  color = "#ffffff",
  size = "small",
  className,
}: ButtonSpinnerProps) {
  return <ActivityIndicator size={size} color={color} className={className} />;
}

export interface ButtonGroupProps {
  space?: "xs" | "sm" | "md" | "lg";
  direction?: "row" | "column";
  className?: string;
  children?: React.ReactNode;
}

export function ButtonGroup({
  space = "sm",
  direction = "row",
  className,
  children,
}: ButtonGroupProps) {
  const gapClasses = {
    xs: "gap-1",
    sm: "gap-2",
    md: "gap-3",
    lg: "gap-4",
  };

  return (
    <View
      className={clsx(
        direction === "row" ? "flex-row items-center" : "flex-col",
        gapClasses[space],
        className
      )}
    >
      {children}
    </View>
  );
}
