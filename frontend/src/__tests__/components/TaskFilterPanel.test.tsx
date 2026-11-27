import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { TaskFilterPanel } from "@/components/tasks/TaskFilterPanel";
import type { TaskListFilters } from "@/types/task";
import { loadTasksModuleConfig, storeTasksModuleConfig } from "@/lib/tasks/tasksModuleConfig";

jest.mock("@/lib/api/orgCustomizationApi", () => ({ fetchOptionConfig: jest.fn().mockResolvedValue({ options: [], can_edit: true }) }));

describe("TaskFilterPanel", () => {
  const baseFilters: TaskListFilters = {};

  it("shows Filter badge with active count", () => {
    render(
      <TaskFilterPanel
        filters={{ ...baseFilters, status: "DRAFT", priority: "HIGH" }}
        onChange={() => {}}
        onClearAll={() => {}}
      />,
    );

    const button = screen.getByRole("button", { name: /Filter/i });
    fireEvent.click(button);
    // Jira-style trigger now uses numeric badge, not "Filter N" text
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("calls onClearAll when Clear all is clicked", () => {
    const onClearAll = jest.fn();
    render(
      <TaskFilterPanel
        filters={{ ...baseFilters, status: "DRAFT" }}
        onChange={() => {}}
        onClearAll={onClearAll}
      />,
    );

    const button = screen.getByRole("button", { name: /Filter/ });
    fireEvent.click(button);
    fireEvent.click(screen.getByText(/Clear all/));
    expect(onClearAll).toHaveBeenCalled();
  });

  it("shows per-category selected count badges in the left panel", () => {
    render(
      <TaskFilterPanel
        filters={{ ...baseFilters, status: ["DRAFT", "SUBMITTED"] }}
        onChange={() => {}}
        onClearAll={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Filter/i }));
    // Status category should show count=2
    expect(screen.getAllByText("2").length).toBeGreaterThan(0);
  });

  it("stays open after a controlled filter selection updates props", () => {
    const ControlledPanel = () => {
      const [filters, setFilters] = React.useState<TaskListFilters>({});

      return (
        <TaskFilterPanel
          filters={filters}
          onChange={setFilters}
          onClearAll={() => setFilters({})}
        />
      );
    };

    render(<ControlledPanel />);

    fireEvent.click(screen.getByRole("button", { name: /Filter/i }));
    fireEvent.click(screen.getByRole("button", { name: /Status/i }));
    fireEvent.click(screen.getByLabelText("Draft"));

    expect(screen.getByRole("button", { name: /Close/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Draft")).toBeChecked();
  });

  it("shows task tags as filter options", () => {
    const onChange = jest.fn();
    render(
      <TaskFilterPanel
        filters={{ ...baseFilters }}
        onChange={onChange}
        onClearAll={() => {}}
        tagOptions={[{ name: "Launch", color: "#123ABC" }]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Filter/i }));
    fireEvent.click(screen.getByRole("button", { name: /Tags/i }));
    fireEvent.click(screen.getByLabelText("Launch"));

    expect(onChange).toHaveBeenCalledWith({ tag_names: ["Launch"] });
  });

  describe("with tasks module config", () => {
    const option = (field: string, value: string, enabled: boolean, order: number) => ({
      field, value, default_label: value, label: null, color: null, enabled, order, is_default: false, editable: [],
    });

    beforeAll(() => loadTasksModuleConfig(1));

    afterEach(() => storeTasksModuleConfig(1, { options: [], can_edit: true }));

    it("hides disabled filters and opens the first visible one", () => {
      storeTasksModuleConfig(1, { can_edit: true, options: [
        option("filter", "assignee", false, 0),
        option("filter", "tags", false, 5),
      ] });

      render(<TaskFilterPanel filters={baseFilters} onChange={() => {}} onClearAll={() => {}} />);
      fireEvent.click(screen.getByRole("button", { name: /Filter/i }));

      expect(screen.queryByTestId("task-filter-tab-assignee")).not.toBeInTheDocument();
      expect(screen.queryByTestId("task-filter-tab-tags")).not.toBeInTheDocument();
      expect(screen.getByTestId("task-filter-tab-owner")).toHaveClass("font-medium");
    });

    it("hides disabled statuses from the status filter", () => {
      storeTasksModuleConfig(1, { can_edit: true, options: [option("filter_status", "LOCKED", false, 5)] });

      render(<TaskFilterPanel filters={baseFilters} onChange={() => {}} onClearAll={() => {}} />);
      fireEvent.click(screen.getByRole("button", { name: /Filter/i }));
      fireEvent.click(screen.getByTestId("task-filter-tab-status"));

      expect(screen.getByTestId("task-filter-option-status-DRAFT")).toBeInTheDocument();
      expect(screen.queryByTestId("task-filter-option-status-LOCKED")).not.toBeInTheDocument();
    });
  });
});
