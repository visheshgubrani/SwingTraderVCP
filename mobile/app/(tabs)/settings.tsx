import React from "react";
import { ScrollView, Alert, RefreshControl } from "react-native";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Box } from "@/components/ui/box";
import { Text } from "@/components/ui/text";
import { Heading } from "@/components/ui/heading";
import { VStack } from "@/components/ui/vstack";
import { HStack } from "@/components/ui/hstack";
import { Card, CardHeader, CardBody, CardFooter } from "@/components/ui/card";
import { Badge, BadgeText } from "@/components/ui/badge";
import { Button, ButtonText, ButtonIcon } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Spinner } from "@/components/ui/spinner";
import { AlertCircleIcon } from "@/components/ui/icon";
import { api } from "@/lib/api";
import { APP_CONFIG } from "@/lib/config";

export default function SettingsScreen() {
  const queryClient = useQueryClient();

  const {
    data: execStatus,
    isLoading: isExecLoading,
    refetch: refetchExec,
  } = useQuery({
    queryKey: ["execution-status"],
    queryFn: async () => {
      try {
        return await api.getExecutionStatus();
      } catch {
        return {
          execution_mode: "paper" as const,
          live_order_placement_enabled: false,
          required_confirmation: "CONFIRM_PAPER_TRADE",
        };
      }
    },
  });

  const {
    data: killSwitch,
    isLoading: isKillLoading,
    refetch: refetchKill,
  } = useQuery({
    queryKey: ["kill-switch"],
    queryFn: async () => {
      try {
        return await api.getKillSwitch();
      } catch {
        return { active: false, reason: "Normal operations" };
      }
    },
  });

  const killSwitchMutation = useMutation({
    mutationFn: async (active: boolean) => {
      return await api.updateKillSwitch(active, active ? "Engaged from Mobile App" : "Disarmed from Mobile App");
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["kill-switch"] });
      Alert.alert(
        data.active ? "KILL SWITCH ENGAGED" : "Kill Switch Disarmed",
        data.active
          ? "All automated order placement is blocked. Existing triggers are suspended."
          : "Automated entry supervisor and position management are active."
      );
    },
    onError: (err: any) => {
      Alert.alert("Failed to update Kill Switch", err?.message || "Communication error.");
    },
  });

  const handleToggleKillSwitch = (currentActive: boolean) => {
    const nextActive = !currentActive;
    Alert.alert(
      nextActive ? "Engage Emergency Kill Switch?" : "Disarm Kill Switch?",
      nextActive
        ? "Engaging the kill switch will immediately block all entry triggers and order submissions."
        : "Are you sure you want to resume normal trading operations?",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: nextActive ? "Engage Kill Switch" : "Disarm",
          style: nextActive ? "destructive" : "default",
          onPress: () => killSwitchMutation.mutate(nextActive),
        },
      ]
    );
  };

  const isRefreshing = isExecLoading || isKillLoading;

  return (
    <Box className="flex-1 bg-background">
      <ScrollView
        className="flex-1 p-4"
        refreshControl={
          <RefreshControl
            refreshing={isRefreshing}
            onRefresh={() => {
              refetchExec();
              refetchKill();
            }}
            tintColor="#fafafa"
          />
        }
      >
        <VStack space="lg">
          {/* Emergency Kill Switch Card */}
          <Card
            className={
              killSwitch?.active
                ? "bg-destructive/20 border-destructive"
                : "bg-card border-border/80"
            }
            size="md"
          >
            <CardHeader>
              <HStack className="justify-between items-center">
                <HStack space="xs" className="items-center">
                  <AlertCircleIcon className={killSwitch?.active ? "text-destructive" : "text-foreground"} />
                  <Heading size="lg" bold className={killSwitch?.active ? "text-destructive" : "text-foreground"}>
                    Emergency Kill Switch
                  </Heading>
                </HStack>
                <Badge
                  variant={killSwitch?.active ? "destructive" : "success"}
                  size="md"
                >
                  <BadgeText>
                    {killSwitch?.active ? "ENGAGED" : "ARMED / SAFE"}
                  </BadgeText>
                </Badge>
              </HStack>
            </CardHeader>
            <CardBody>
              <Text size="sm" className="text-muted-foreground mb-3">
                When engaged, the kill switch prevents any order placement across all
                workers, disables supervisor triggers, and halts live entries.
              </Text>
              <HStack className="justify-between items-center pt-2 border-t border-border/40">
                <Text size="sm" bold>
                  Kill Switch State:
                </Text>
                <Switch
                  value={killSwitch?.active ?? false}
                  onValueChange={() =>
                    handleToggleKillSwitch(killSwitch?.active ?? false)
                  }
                  disabled={killSwitchMutation.isPending}
                />
              </HStack>
            </CardBody>
          </Card>

          {/* Execution Environment Card */}
          <Card className="bg-card border-border/80" size="md">
            <CardHeader>
              <Heading size="md" bold>
                Trading Execution Mode
              </Heading>
            </CardHeader>
            <CardBody>
              <VStack space="sm">
                <HStack className="justify-between items-center">
                  <Text size="sm" className="text-muted-foreground">
                    Current Mode:
                  </Text>
                  <Badge
                    variant={
                      execStatus?.execution_mode === "live"
                        ? "destructive"
                        : "info"
                    }
                    size="md"
                  >
                    <BadgeText className="uppercase">
                      {execStatus?.execution_mode ?? "PAPER"} MODE
                    </BadgeText>
                  </Badge>
                </HStack>

                <HStack className="justify-between items-center">
                  <Text size="sm" className="text-muted-foreground">
                    Live Placement Enabled:
                  </Text>
                  <Badge
                    variant={
                      execStatus?.live_order_placement_enabled
                        ? "success"
                        : "default"
                    }
                    size="sm"
                  >
                    <BadgeText>
                      {execStatus?.live_order_placement_enabled
                        ? "Double-Armed"
                        : "Blocked"}
                    </BadgeText>
                  </Badge>
                </HStack>

                <HStack className="justify-between items-center">
                  <Text size="sm" className="text-muted-foreground">
                    Broker (Fyers API):
                  </Text>
                  <Badge variant="success" size="sm">
                    <BadgeText>CNC Equity</BadgeText>
                  </Badge>
                </HStack>
              </VStack>
            </CardBody>
          </Card>

          {/* Backend Diagnostics Card */}
          <Card className="bg-card border-border/80" size="md">
            <CardHeader>
              <Heading size="md" bold>
                System Diagnostics
              </Heading>
            </CardHeader>
            <CardBody>
              <VStack space="xs">
                <HStack className="justify-between items-center">
                  <Text size="xs" className="text-muted-foreground">
                    Target Backend:
                  </Text>
                  <Text size="xs" bold>
                    {APP_CONFIG.apiBaseUrl}
                  </Text>
                </HStack>
                <HStack className="justify-between items-center">
                  <Text size="xs" className="text-muted-foreground">
                    Timezone:
                  </Text>
                  <Text size="xs" bold>
                    {APP_CONFIG.marketTimezone}
                  </Text>
                </HStack>
                <HStack className="justify-between items-center">
                  <Text size="xs" className="text-muted-foreground">
                    App Version:
                  </Text>
                  <Text size="xs" bold>
                    v{APP_CONFIG.version} (Expo Native)
                  </Text>
                </HStack>
              </VStack>
            </CardBody>
          </Card>
        </VStack>
      </ScrollView>
    </Box>
  );
}
