import { Link, Stack } from "expo-router";
import { Center } from "@/components/ui/center";
import { Heading } from "@/components/ui/heading";
import { Text } from "@/components/ui/text";
import { Button, ButtonText } from "@/components/ui/button";
import { VStack } from "@/components/ui/vstack";

export default function NotFoundScreen() {
  return (
    <>
      <Stack.Screen options={{ title: "Page Not Found" }} />
      <Center className="flex-1 p-6 bg-background">
        <VStack space="lg" className="items-center max-w-sm text-center">
          <Heading size="xl">Screen doesn't exist</Heading>
          <Text className="text-muted-foreground text-center">
            The screen you are looking for could not be found or has been moved.
          </Text>
          <Link href="/(tabs)" asChild>
            <Button variant="default">
              <ButtonText>Go to Watchlist</ButtonText>
            </Button>
          </Link>
        </VStack>
      </Center>
    </>
  );
}
