namespace DecisionService.Decisions;

public static class DecisionPermissionPolicy
{
    public const int SuperuserLevel = 0;
    public const int EditMaxLevel = 13;
    public const int ViewMaxLevel = 999;
    public const int ApprovalReviewMaxLevel = 8;

    private static readonly Dictionary<string, int> RoleLevels = new(StringComparer.OrdinalIgnoreCase)
    {
        ["owner"] = 1,
        ["Super Administrator"] = 1,
        ["Organization Admin"] = 2,
        ["Team Leader"] = 3,
        ["Campaign Manager"] = 4,
        ["Budget Controller"] = 5,
        ["Approver"] = 6,
        ["Reviewer"] = 7,
        ["Data Analyst"] = 8,
        ["member"] = 8,
        ["Senior Media Buyer"] = 9,
        ["Specialist Media Buyer"] = 10,
        ["Junior Media Buyer"] = 11,
        ["Designer"] = 12,
        ["Copywriter"] = 13,
        ["viewer"] = 999,
    };

    public static int RoleLevel(string? role)
    {
        var normalized = (role ?? string.Empty).Trim();
        return RoleLevels.TryGetValue(normalized, out var level)
            ? level
            : RoleLevels["viewer"];
    }

    public static int RequiredLevel(DecisionAccessAction action) => action switch
    {
        DecisionAccessAction.Edit => EditMaxLevel,
        DecisionAccessAction.Approve => ApprovalReviewMaxLevel,
        _ => ViewMaxLevel,
    };

    public static bool IsAllowed(string? role, DecisionAccessAction action)
    {
        return RoleLevel(role) <= RequiredLevel(action);
    }
}
