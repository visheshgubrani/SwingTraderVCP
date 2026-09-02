import React, { createContext, useContext } from "react";
import {
  View,
  TextInput as RNTextInput,
  Pressable as RNPressable,
  type TextInputProps,
  type ViewProps,
  type PressableProps,
} from "react-native";
import { clsx } from "clsx";

export type InputSize = "sm" | "md" | "lg" | "xl";
export type InputVariant = "outline" | "underlined" | "rounded";

interface InputContextValue {
  size: InputSize;
  variant: InputVariant;
  isDisabled?: boolean;
  isInvalid?: boolean;
}

const InputContext = createContext<InputContextValue>({
  size: "md",
  variant: "outline",
});

export interface InputProps extends ViewProps {
  size?: InputSize;
  variant?: InputVariant;
  isDisabled?: boolean;
  isInvalid?: boolean;
  className?: string;
  children?: React.ReactNode;
}

const sizeContainerClasses: Record<InputSize, string> = {
  sm: "h-9 px-2.5",
  md: "h-11 px-3",
  lg: "h-12 px-4",
  xl: "h-14 px-4",
};

const variantContainerClasses: Record<InputVariant, string> = {
  outline: "border border-input rounded-lg bg-card",
  underlined: "border-b border-input bg-transparent rounded-none px-0",
  rounded: "border border-input rounded-full bg-card px-4",
};

export const Input = React.forwardRef<View, InputProps>(function Input(
  {
    size = "md",
    variant = "outline",
    isDisabled = false,
    isInvalid = false,
    className,
    children,
    ...props
  },
  ref
) {
  return (
    <InputContext.Provider value={{ size, variant, isDisabled, isInvalid }}>
      <View
        ref={ref}
        className={clsx(
          "flex-row items-center",
          sizeContainerClasses[size],
          variantContainerClasses[variant],
          isInvalid && "border-destructive",
          isDisabled && "opacity-50 pointer-events-none",
          className
        )}
        {...props}
      >
        {children}
      </View>
    </InputContext.Provider>
  );
});

Input.displayName = "Input";

export interface InputFieldProps extends TextInputProps {
  className?: string;
}

export const InputField = React.forwardRef<RNTextInput, InputFieldProps>(
  function InputField({ className, placeholderTextColor, ...props }, ref) {
    const { isDisabled } = useContext(InputContext);

    return (
      <RNTextInput
        ref={ref}
        editable={!isDisabled}
        placeholderTextColor={placeholderTextColor ?? "#a1a1aa"}
        className={clsx(
          "flex-1 text-foreground text-base py-0 px-2 h-full",
          className
        )}
        {...props}
      />
    );
  }
);

InputField.displayName = "InputField";

export interface InputSlotProps extends PressableProps {
  className?: string;
  children?: React.ReactNode;
}

export const InputSlot = React.forwardRef<View, InputSlotProps>(
  function InputSlot({ className, children, ...props }, ref) {
    return (
      <RNPressable
        ref={ref}
        className={clsx("justify-center items-center px-1", className)}
        {...props}
      >
        {children}
      </RNPressable>
    );
  }
);

InputSlot.displayName = "InputSlot";

export interface InputIconProps {
  as: React.ComponentType<any>;
  size?: number;
  color?: string;
  className?: string;
}

export function InputIcon({
  as: IconComponent,
  size = 18,
  color,
  className,
}: InputIconProps) {
  return (
    <IconComponent
      size={size}
      color={color}
      className={clsx("text-muted-foreground", className)}
    />
  );
}
