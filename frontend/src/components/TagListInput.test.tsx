import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { TagListInput } from "./TagListInput";

function Harness({ initial = [] as string[], onSubmit = vi.fn() }) {
  const [items, setItems] = useState(initial);
  const [draft, setDraft] = useState("");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <TagListInput label="Interests" singular="interest" items={items} draft={draft} onItemsChange={setItems} onDraftChange={setDraft} />
      <button type="submit">Send form</button>
    </form>
  );
}

const input = () => screen.getByLabelText("New interest");

describe("TagListInput", () => {
  it("is a labelled group with a labelled input and Add button", () => {
    render(<Harness />);
    expect(screen.getByRole("group", { name: "Interests" })).toBeInTheDocument();
    expect(input()).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add interest" })).toBeInTheDocument();
    expect(screen.getByText("None added yet.")).toBeInTheDocument();
  });

  it("adds with the button, trims, clears the box and announces it", () => {
    render(<Harness />);
    fireEvent.change(input(), { target: { value: "  Hiking " } });
    fireEvent.click(screen.getByRole("button", { name: "Add interest" }));
    expect(screen.getByRole("list", { name: "Interests added" })).toHaveTextContent("Hiking");
    expect(input()).toHaveValue("");
    expect(screen.getByRole("status")).toHaveTextContent("Added Hiking to interests.");
  });

  it("adds with Enter and does not send the surrounding form", () => {
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);
    fireEvent.change(input(), { target: { value: "Chess" } });
    fireEvent.keyDown(input(), { key: "Enter" });
    expect(screen.getByText("Chess")).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("removes an entry with a button that names it, and announces it", () => {
    render(<Harness initial={["Chess", "Tea"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove Chess from interests" }));
    expect(screen.queryByText("Chess")).not.toBeInTheDocument();
    expect(screen.getByText("Tea")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Removed Chess from interests.");
  });

  it("explains a blank or repeated entry, and clears the message when typing again", () => {
    render(<Harness initial={["Chess"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Add interest" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter something to add.");
    fireEvent.change(input(), { target: { value: "chess" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add interest" }));
    expect(screen.getByRole("alert")).toHaveTextContent("That is already in the list.");
    expect(input()).toHaveAttribute("aria-invalid", "true");
  });
});
