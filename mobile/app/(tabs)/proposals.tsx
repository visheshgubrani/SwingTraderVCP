import React from "react";
import { FlatList, RefreshControl, Alert } from "react-native";
import { useRouter } from "expo-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Box } from "@/components/ui/box";
import { Text } from "@/components/ui/text";
import { Heading } from "@/components/ui/heading";
import { VStack } from "@/components/ui/vstack";
import { HStack } from "@/components/ui/hstack";
import { Card, CardHeader, CardBody, CardFooter } from "@/components/ui/card";
import { Badge, BadgeText } from "@/components/ui/badge";
import { Button, ButtonText, ButtonIcon } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { CheckIcon, XIcon, ChevronRightIcon } from "@/components/ui/icon";
import { api } from "@/lib/api";
import { formatINR, formatRelativeTime } from "@/lib/utils";
import type { TradeProposal } from "@/types";

export default function ProposalsScreen() {
  const router = useRouter();
  const queryClient = useQueryClient();

  const {
    data: proposals,
    isLoading,
    isRefetching,
    refetch,
  } = useQuery({
    queryKey: ["proposals"],
    queryFn: async () => {
      try {
        return await api.getProposals();
      } catch {
        // Fallback demo proposals for initial app scaffolding
        return [
          {
            id: "prop-1",
            symbol: "DIXON",
            pattern_type: "VCP 3T (14w)",
            classification: "valid",
            status: "proposed",
            pivot_price: 13540.0,
            stop_loss: 13120.0,
            target_1: 14380.0,
            target_2: 14800.0,
            risk_per_share: 420.0,
            risk_reward_ratio: 2.0,
            suggested_quantity: 12,
            max_risk_budget: 5000.0,
            ai_confidence: 0.88,
            ai_notes: "Clean 3-contraction sequence with volume dry-up on final pull-back.",
            created_at: new Date().toISOString(),
            expires_at: new Date(Date.now() + 3600000 * 12).toISOString(),
          },
          {
            id: "prop-2",
            symbol: "TRENT",
            pattern_type: "VCP 2T (8w)",
            classification: "valid",
            status: "approved",
            pivot_price: 6920.0,
            stop_loss: 6710.0,
            target_1: 7340.0,
            target_2: 7550.0,
            risk_per_share: 210.0,
            risk_reward_ratio: 2.0,
            suggested_quantity: 23,
            max_risk_budget: 5000.0,
            ai_confidence: 0.92,
            ai_notes: "Strong relative strength vs Nifty 500. EMA21 dynamic support held.",
            created_at: new Date(Date.now() - 3600000 * 2).toISOString(),
            expires_at: new Date(Date.now() + 3600000 * 10).toISOString(),
          },
        ] as TradeProposal[];
      }
    },
  });

  const confirmMutation = useMutation({
    mutationFn: async ({ id, approved }: { id: string; approved: boolean }) => {
      const confirmationText = approved ? "CONFIRM_TRADE_INSTRUCTION" : "REJECT_TRADE_INSTRUCTION";
      return await api.confirmProposal(id, confirmationText);
    },
    onSuccess: (_, variables) => {
      Alert.alert(
        variables.approved ? "Proposal Approved" : "Proposal Rejected",
        `Trade instruction has been successfully ${variables.approved ? "armed for supervisor trigger" : "rejected"}.`
      );
      queryClient.invalidateQueries({ queryKey: ["proposals"] });
    },
    onError: (error: any) => {
      Alert.alert("Action Failed", error?.message || "Failed to update proposal status.");
    },
  });

  const handleApprove = (item: TradeProposal) => {
    Alert.alert(
      `Approve ${item.symbol} Plan?`,
      `Pivot: ${formatINR(item.pivot_price)}\nStop Loss: ${formatINR(item.stop_loss)}\nMax Risk: ${formatINR(item.max_risk_budget)}\nQty: ${item.suggested_quantity} shares`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Approve Plan",
          style: "default",
          onPress: () => confirmMutation.mutate({ id: item.id, approved: true }),
        },
      ]
    );
  };

  const handleReject = (item: TradeProposal) => {
    Alert.alert(
      `Reject ${item.symbol}?`,
      "This plan will be cancelled and will not be monitored for entry triggers.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Reject",
          style: "destructive",
          onPress: () => confirmMutation.mutate({ id: item.id, approved: false }),
        },
      ]
    );
  };

  return (
    <Box className="flex-1 bg-background p-4">
      <VStack space="md" className="flex-1">
        {/* Header Notice */}
        <HStack className="justify-between items-center px-1">
          <Text size="sm" className="text-muted-foreground">
            P10 Proposal Checkpoint
          </Text>
          <Badge variant="warning" size="sm">
            <BadgeText>Deadline 09:00 IST</BadgeText>
          </Badge>
        </HStack>

        {isLoading ? (
          <Box className="flex-1 justify-center items-center">
            <Spinner size="large" />
            <Text className="text-muted-foreground mt-3">Loading Proposals...</Text>
          </Box>
        ) : (
          <FlatList
            data={proposals || []}
            keyExtractor={(item) => item.id}
            refreshControl={
              <RefreshControl
                refreshing={isRefetching}
                onRefresh={refetch}
                tintColor="#fafafa"
              />
            }
            renderItem={({ item }) => {
              const isPending = item.status === "proposed";
              const isApproved = item.status === "approved";

              return (
                <Card className="mb-4 bg-card border-border/80" size="md">
                  <CardHeader>
                    <HStack className="justify-between items-center">
                      <VStack space="xs">
                        <HStack space="xs" className="items-center">
                          <Heading size="lg" bold>
                            {item.symbol}
                          </Heading>
                          <Badge
                            variant={
                              isApproved
                                ? "success"
                                : isPending
                                ? "warning"
                                : "default"
                            }
                            size="sm"
                          >
                            <BadgeText className="uppercase">
                              {item.status}
                            </BadgeText>
                          </Badge>
                        </HStack>
                        <Text size="xs" className="text-muted-foreground">
                          {item.pattern_type} • {formatRelativeTime(item.created_at)}
                        </Text>
                      </VStack>

                      {item.ai_confidence && (
                        <Badge variant="info" size="sm">
                          <BadgeText>
                            AI {Math.round(item.ai_confidence * 100)}%
                          </BadgeText>
                        </Badge>
                      )}
                    </HStack>
                  </CardHeader>

                  <CardBody>
                    <VStack space="sm" className="bg-secondary/40 p-3 rounded-lg border border-border/40">
                      <HStack className="justify-between items-center">
                        <Text size="sm" className="text-muted-foreground">
                          Pivot Buy Level:
                        </Text>
                        <Text size="sm" bold className="text-foreground">
                          {formatINR(item.pivot_price)}
                        </Text>
                      </HStack>

                      <HStack className="justify-between items-center">
                        <Text size="sm" className="text-muted-foreground">
                          Stop Loss:
                        </Text>
                        <Text size="sm" bold className="text-destructive">
                          {formatINR(item.stop_loss)}
                        </Text>
                      </HStack>

                      <HStack className="justify-between items-center">
                        <Text size="sm" className="text-muted-foreground">
                          Target 1 (R:R {item.risk_reward_ratio}R):
                        </Text>
                        <Text size="sm" bold className="text-success">
                          {formatINR(item.target_1)}
                        </Text>
                      </HStack>

                      <HStack className="justify-between items-center pt-1 border-t border-border/30">
                        <Text size="xs" className="text-muted-foreground">
                          Size: {item.suggested_quantity} shares
                        </Text>
                        <Text size="xs" className="text-muted-foreground">
                          Max Risk: {formatINR(item.max_risk_budget)}
                        </Text>
                      </HStack>
                    </VStack>

                    {item.ai_notes && (
                      <Text size="xs" className="text-muted-foreground mt-2 italic">
                        "{item.ai_notes}"
                      </Text>
                    )}
                  </CardBody>

                  <CardFooter className="gap-2">
                    {isPending ? (
                      <HStack space="sm" className="flex-1 justify-end">
                        <Button
                          variant="outline"
                          size="sm"
                          onPress={() => handleReject(item)}
                          disabled={confirmMutation.isPending}
                        >
                          <ButtonIcon as={XIcon} />
                          <ButtonText>Reject</ButtonText>
                        </Button>

                        <Button
                          variant="success"
                          size="sm"
                          onPress={() => handleApprove(item)}
                          disabled={confirmMutation.isPending}
                        >
                          <ButtonIcon as={CheckIcon} />
                          <ButtonText>Approve Plan</ButtonText>
                        </Button>
                      </HStack>
                    ) : (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="w-full justify-between"
                        onPress={() => router.push(`/proposal/${item.id}`)}
                      >
                        <ButtonText>View Execution Status</ButtonText>
                        <ButtonIcon as={ChevronRightIcon} />
                      </Button>
                    )}
                  </CardFooter>
                </Card>
              );
            }}
          />
        )}
      </VStack>
    </Box>
  );
}
