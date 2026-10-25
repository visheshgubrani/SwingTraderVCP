import { describe, expect, it } from "vitest"
import { useLocation } from "react-router"

import { useUrlState } from "@/lib/useUrlState"
import { renderWithProviders, screen } from "@/test/test-utils"

function Harness() {
  const [state, setState] = useUrlState({ tab: "pending", q: "" })
  const location = useLocation()
  return (
    <div>
      <span data-testid="state">{`${state.tab}|${state.q}`}</span>
      <span data-testid="search">{location.search}</span>
      <button onClick={() => setState({ tab: "history", q: "infy" })} type="button">set</button>
      <button onClick={() => setState({ tab: "pending", q: null })} type="button">reset</button>
    </div>
  )
}

describe("useUrlState", () => {
  it("reads defaults, writes changes and drops values equal to the default", async () => {
    const { user } = renderWithProviders(<Harness />, { routerInitialEntries: ["/proposals?keep=1"] })
    expect(screen.getByTestId("state")).toHaveTextContent("pending|")

    await user.click(screen.getByText("set"))
    expect(screen.getByTestId("state")).toHaveTextContent("history|infy")
    expect(screen.getByTestId("search")).toHaveTextContent("?keep=1&tab=history&q=infy")

    await user.click(screen.getByText("reset"))
    expect(screen.getByTestId("search")).toHaveTextContent("?keep=1")
  })
})
