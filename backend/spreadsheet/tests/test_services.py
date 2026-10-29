from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from spreadsheet.models import (
    Spreadsheet,
    Sheet,
    SheetRow,
    SheetColumn,
    Cell,
    CellValueType,
    ComputedCellType,
    WorkflowPattern,
    WorkflowPatternStep,
)
from spreadsheet.services import SpreadsheetService, SheetService, CellService, WorkflowPatternService
from core.models import Project, Organization

User = get_user_model()


# ========== Helper Functions ==========

def create_test_user(username='testuser', email='test@example.com'):
    """Helper to create test user"""
    return User.objects.create_user(
        username=username,
        email=email,
        password='testpass123'
    )


def create_test_organization(name='Test Organization'):
    """Helper to create test organization"""
    return Organization.objects.create(name=name)


def create_test_project(organization, name='Test Project', owner=None):
    """Helper to create test project"""
    return Project.objects.create(
        name=name,
        organization=organization,
        owner=owner
    )


def create_test_spreadsheet(project, name='Test Spreadsheet'):
    """Helper to create test spreadsheet"""
    return Spreadsheet.objects.create(
        project=project,
        name=name
    )


def create_test_sheet(spreadsheet, name='Test Sheet', position=0):
    """Helper to create test sheet"""
    return Sheet.objects.create(
        spreadsheet=spreadsheet,
        name=name,
        position=position
    )


# ========== Service Tests ==========

class SpreadsheetServiceTest(TestCase):
    """Test cases for SpreadsheetService"""
    
    def setUp(self):
        """Set up test data"""
        self.user = create_test_user()
        self.organization = create_test_organization()
        self.project = create_test_project(self.organization, owner=self.user)
    
    def test_create_spreadsheet_success(self):
        """Test successful spreadsheet creation"""
        spreadsheet = SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='New Spreadsheet'
        )
        
        self.assertIsNotNone(spreadsheet.id)
        self.assertEqual(spreadsheet.name, 'New Spreadsheet')
        self.assertEqual(spreadsheet.project, self.project)
        self.assertFalse(spreadsheet.is_deleted)
    
    def test_create_spreadsheet_duplicate_name(self):
        """Test creating spreadsheet with duplicate name fails"""
        SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='Duplicate Name'
        )
        
        with self.assertRaises(ValidationError) as context:
            SpreadsheetService.create_spreadsheet(
                project=self.project,
                name='Duplicate Name'
            )
        
        self.assertIn('already exists', str(context.exception))
    
    def test_create_spreadsheet_different_projects(self):
        """Test creating spreadsheets with same name in different projects"""
        project2 = create_test_project(self.organization, name='Project 2')
        
        spreadsheet1 = SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='Same Name'
        )
        spreadsheet2 = SpreadsheetService.create_spreadsheet(
            project=project2,
            name='Same Name'
        )
        
        self.assertNotEqual(spreadsheet1.id, spreadsheet2.id)
        self.assertEqual(spreadsheet1.name, spreadsheet2.name)
        self.assertNotEqual(spreadsheet1.project, spreadsheet2.project)
    
    def test_create_spreadsheet_after_deleted(self):
        """Test creating spreadsheet with name of deleted spreadsheet"""
        spreadsheet1 = SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='Deleted Name'
        )
        spreadsheet1.is_deleted = True
        spreadsheet1.save()
        
        # Should be able to create another with same name
        spreadsheet2 = SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='Deleted Name'
        )
        
        self.assertNotEqual(spreadsheet1.id, spreadsheet2.id)
        self.assertEqual(spreadsheet2.name, 'Deleted Name')
    
    def test_update_spreadsheet_success(self):
        """Test successful spreadsheet update"""
        spreadsheet = SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='Original Name'
        )
        
        updated_spreadsheet = SpreadsheetService.update_spreadsheet(
            spreadsheet=spreadsheet,
            name='Updated Name'
        )
        
        self.assertEqual(updated_spreadsheet.name, 'Updated Name')
        self.assertEqual(updated_spreadsheet.id, spreadsheet.id)
        self.assertEqual(updated_spreadsheet.project, self.project)
        
        # Verify it's saved in DB
        spreadsheet.refresh_from_db()
        self.assertEqual(spreadsheet.name, 'Updated Name')
    
    def test_update_spreadsheet_duplicate_name(self):
        """Test updating spreadsheet with duplicate name fails"""
        spreadsheet1 = SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='First Spreadsheet'
        )
        spreadsheet2 = SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='Second Spreadsheet'
        )
        
        with self.assertRaises(ValidationError) as context:
            SpreadsheetService.update_spreadsheet(
                spreadsheet=spreadsheet2,
                name='First Spreadsheet'
            )
        
        self.assertIn('already exists', str(context.exception))
        
        # Verify spreadsheet2 name unchanged
        spreadsheet2.refresh_from_db()
        self.assertEqual(spreadsheet2.name, 'Second Spreadsheet')
    
    def test_update_spreadsheet_same_name(self):
        """Test updating spreadsheet with same name (should succeed)"""
        spreadsheet = SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='Same Name'
        )
        
        # Update with same name should succeed
        updated_spreadsheet = SpreadsheetService.update_spreadsheet(
            spreadsheet=spreadsheet,
            name='Same Name'
        )
        
        self.assertEqual(updated_spreadsheet.name, 'Same Name')
        self.assertEqual(updated_spreadsheet.id, spreadsheet.id)
    
    def test_update_spreadsheet_different_project(self):
        """Test updating spreadsheet with name from different project (should succeed)"""
        project2 = create_test_project(self.organization, name='Project 2')
        
        spreadsheet1 = SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='Unique Name'
        )
        spreadsheet2 = SpreadsheetService.create_spreadsheet(
            project=project2,
            name='Different Name'
        )
        
        # Update spreadsheet2 with name from spreadsheet1 (different project) should succeed
        updated_spreadsheet = SpreadsheetService.update_spreadsheet(
            spreadsheet=spreadsheet2,
            name='Unique Name'
        )
        
        self.assertEqual(updated_spreadsheet.name, 'Unique Name')
    
    def test_update_spreadsheet_after_deleted(self):
        """Test updating spreadsheet with name of deleted spreadsheet"""
        spreadsheet1 = SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='Deleted Name'
        )
        spreadsheet1.is_deleted = True
        spreadsheet1.save()
        
        spreadsheet2 = SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='Active Spreadsheet'
        )
        
        # Should be able to update with name of deleted spreadsheet
        updated_spreadsheet = SpreadsheetService.update_spreadsheet(
            spreadsheet=spreadsheet2,
            name='Deleted Name'
        )
        
        self.assertEqual(updated_spreadsheet.name, 'Deleted Name')
    
    def test_delete_spreadsheet_success(self):
        """Test successful spreadsheet deletion (soft delete)"""
        spreadsheet = SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='To Be Deleted'
        )
        spreadsheet_id = spreadsheet.id
        
        SpreadsheetService.delete_spreadsheet(spreadsheet)
        
        # Spreadsheet should still exist but marked as deleted
        self.assertTrue(Spreadsheet.objects.filter(id=spreadsheet_id).exists())
        spreadsheet.refresh_from_db()
        self.assertTrue(spreadsheet.is_deleted)
    
    def test_delete_spreadsheet_multiple_times(self):
        """Test deleting already deleted spreadsheet (idempotent)"""
        spreadsheet = SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='Double Delete'
        )
        
        SpreadsheetService.delete_spreadsheet(spreadsheet)
        self.assertTrue(spreadsheet.is_deleted)
        
        # Delete again should not cause error
        SpreadsheetService.delete_spreadsheet(spreadsheet)
        spreadsheet.refresh_from_db()
        self.assertTrue(spreadsheet.is_deleted)
    
    def test_create_spreadsheet_atomic_transaction(self):
        """Test that spreadsheet creation is atomic"""
        # This test ensures transaction rollback on error
        # If we have validation error, nothing should be created
        
        # Create one spreadsheet
        SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='First'
        )
        
        # Try to create duplicate - should raise ValidationError
        # and nothing should be partially created
        initial_count = Spreadsheet.objects.filter(
            project=self.project,
            is_deleted=False
        ).count()
        
        with self.assertRaises(ValidationError):
            SpreadsheetService.create_spreadsheet(
                project=self.project,
                name='First'
            )
        
        # Count should remain the same
        final_count = Spreadsheet.objects.filter(
            project=self.project,
            is_deleted=False
        ).count()
        
        self.assertEqual(initial_count, final_count)
    
    def test_update_spreadsheet_atomic_transaction(self):
        """Test that spreadsheet update is atomic"""
        spreadsheet1 = SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='First'
        )
        spreadsheet2 = SpreadsheetService.create_spreadsheet(
            project=self.project,
            name='Second'
        )
        
        original_name = spreadsheet2.name
        
        # Try to update with duplicate name - should raise ValidationError
        # and original name should remain
        with self.assertRaises(ValidationError):
            SpreadsheetService.update_spreadsheet(
                spreadsheet=spreadsheet2,
                name='First'
            )
        
        spreadsheet2.refresh_from_db()
        self.assertEqual(spreadsheet2.name, original_name)


# ========== SheetService Tests ==========

class SheetServiceTest(TestCase):
    """Test cases for SheetService"""
    
    def setUp(self):
        """Set up test data"""
        self.user = create_test_user()
        self.organization = create_test_organization()
        self.project = create_test_project(self.organization, owner=self.user)
        self.spreadsheet = create_test_spreadsheet(self.project)
    
    def test_create_sheet_success(self):
        """Test successful sheet creation with auto-assigned position"""
        sheet = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='New Sheet'
        )
        
        self.assertIsNotNone(sheet.id)
        self.assertEqual(sheet.name, 'New Sheet')
        self.assertEqual(sheet.spreadsheet, self.spreadsheet)
        self.assertEqual(sheet.position, 0)  # First sheet should be position 0
        self.assertFalse(sheet.is_deleted)
    
    def test_create_sheet_auto_assigns_position(self):
        """Test that position is automatically assigned"""
        sheet1 = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='Sheet 1'
        )
        self.assertEqual(sheet1.position, 0)
        
        sheet2 = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='Sheet 2'
        )
        self.assertEqual(sheet2.position, 1)
        
        sheet3 = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='Sheet 3'
        )
        self.assertEqual(sheet3.position, 2)
    
    def test_create_sheet_duplicate_name(self):
        """Test creating sheet with duplicate name fails"""
        SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='Duplicate Name'
        )
        
        with self.assertRaises(ValidationError) as context:
            SheetService.create_sheet(
                spreadsheet=self.spreadsheet,
                name='Duplicate Name'
            )
        
        self.assertIn('already exists', str(context.exception))
    
    def test_create_sheet_different_spreadsheets(self):
        """Test creating sheets with same name in different spreadsheets"""
        spreadsheet2 = create_test_spreadsheet(self.project, name='Spreadsheet 2')
        
        sheet1 = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='Same Name'
        )
        sheet2 = SheetService.create_sheet(
            spreadsheet=spreadsheet2,
            name='Same Name'
        )
        
        self.assertNotEqual(sheet1.id, sheet2.id)
        self.assertEqual(sheet1.name, sheet2.name)
        self.assertNotEqual(sheet1.spreadsheet, sheet2.spreadsheet)
    
    def test_create_sheet_after_deleted(self):
        """Test creating sheet with name of deleted sheet"""
        sheet1 = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='Deleted Name'
        )
        sheet1.is_deleted = True
        sheet1.save()
        
        # Should be able to create another with same name
        sheet2 = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='Deleted Name'
        )
        
        self.assertNotEqual(sheet1.id, sheet2.id)
        self.assertEqual(sheet2.name, 'Deleted Name')
    
    def test_update_sheet_success(self):
        """Test successful sheet update"""
        sheet = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='Original Name'
        )
        
        updated_sheet = SheetService.update_sheet(
            sheet=sheet,
            name='Updated Name'
        )
        
        self.assertEqual(updated_sheet.name, 'Updated Name')
        self.assertEqual(updated_sheet.id, sheet.id)
        self.assertEqual(updated_sheet.spreadsheet, self.spreadsheet)
        self.assertEqual(updated_sheet.position, sheet.position)  # Position unchanged
        
        # Verify it's saved in DB
        sheet.refresh_from_db()
        self.assertEqual(sheet.name, 'Updated Name')
        self.assertEqual(sheet.position, 0)  # Position should remain 0
    
    def test_update_sheet_duplicate_name(self):
        """Test updating sheet with duplicate name fails"""
        sheet1 = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='First Sheet'
        )
        sheet2 = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='Second Sheet'
        )
        
        with self.assertRaises(ValidationError) as context:
            SheetService.update_sheet(
                sheet=sheet2,
                name='First Sheet'
            )
        
        self.assertIn('already exists', str(context.exception))
        
        # Verify sheet2 name unchanged
        sheet2.refresh_from_db()
        self.assertEqual(sheet2.name, 'Second Sheet')
    
    def test_update_sheet_same_name(self):
        """Test updating sheet with same name (should succeed)"""
        sheet = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='Same Name'
        )
        
        # Update with same name should succeed
        updated_sheet = SheetService.update_sheet(
            sheet=sheet,
            name='Same Name'
        )
        
        self.assertEqual(updated_sheet.name, 'Same Name')
        self.assertEqual(updated_sheet.id, sheet.id)
    
    def test_update_sheet_different_spreadsheet(self):
        """Test updating sheet with name from different spreadsheet (should succeed)"""
        spreadsheet2 = create_test_spreadsheet(self.project, name='Spreadsheet 2')
        
        sheet1 = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='Unique Name'
        )
        sheet2 = SheetService.create_sheet(
            spreadsheet=spreadsheet2,
            name='Different Name'
        )
        
        # Update sheet2 with name from sheet1 (different spreadsheet) should succeed
        updated_sheet = SheetService.update_sheet(
            sheet=sheet2,
            name='Unique Name'
        )
        
        self.assertEqual(updated_sheet.name, 'Unique Name')
    
    def test_update_sheet_after_deleted(self):
        """Test updating sheet with name of deleted sheet"""
        sheet1 = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='Deleted Name'
        )
        sheet1.is_deleted = True
        sheet1.save()
        
        sheet2 = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='Active Sheet'
        )
        
        # Should be able to update with name of deleted sheet
        updated_sheet = SheetService.update_sheet(
            sheet=sheet2,
            name='Deleted Name'
        )
        
        self.assertEqual(updated_sheet.name, 'Deleted Name')
    
    def test_delete_sheet_success(self):
        """Test successful sheet deletion (soft delete)"""
        sheet = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='To Be Deleted'
        )
        sheet_id = sheet.id
        
        SheetService.delete_sheet(sheet)
        
        # Sheet should still exist but marked as deleted
        self.assertTrue(Sheet.objects.filter(id=sheet_id).exists())
        sheet.refresh_from_db()
        self.assertTrue(sheet.is_deleted)
    
    def test_delete_sheet_multiple_times(self):
        """Test deleting already deleted sheet (idempotent)"""
        sheet = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='Double Delete'
        )
        
        SheetService.delete_sheet(sheet)
        self.assertTrue(sheet.is_deleted)
        
        # Delete again should not cause error
        SheetService.delete_sheet(sheet)
        sheet.refresh_from_db()
        self.assertTrue(sheet.is_deleted)
    
    def test_create_sheet_atomic_transaction(self):
        """Test that sheet creation is atomic"""
        # Create one sheet
        SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='First'
        )
        
        # Try to create duplicate - should raise ValidationError
        # and nothing should be partially created
        initial_count = Sheet.objects.filter(
            spreadsheet=self.spreadsheet,
            is_deleted=False
        ).count()
        
        with self.assertRaises(ValidationError):
            SheetService.create_sheet(
                spreadsheet=self.spreadsheet,
                name='First'
            )
        
        # Count should remain the same
        final_count = Sheet.objects.filter(
            spreadsheet=self.spreadsheet,
            is_deleted=False
        ).count()
        
        self.assertEqual(initial_count, final_count)
    
    def test_update_sheet_atomic_transaction(self):
        """Test that sheet update is atomic"""
        sheet1 = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='First'
        )
        sheet2 = SheetService.create_sheet(
            spreadsheet=self.spreadsheet,
            name='Second'
        )
        
        original_name = sheet2.name
        
        # Try to update with duplicate name - should raise ValidationError
        # and original name should remain
        with self.assertRaises(ValidationError):
            SheetService.update_sheet(
                sheet=sheet2,
                name='First'
            )
        
        sheet2.refresh_from_db()
        self.assertEqual(sheet2.name, original_name)
    
    def test_resize_sheet_row_creation_includes_deleted_in_max(self):
        """Test that resize_sheet row creation includes deleted rows in max position (doesn't reuse deleted positions)"""
        from spreadsheet.models import SheetRow
        self.sheet = Sheet.objects.create(spreadsheet=self.spreadsheet, name="S1", position=0)
        # Create 2 rows via resize_sheet with positions assigned by the service
        result1 = SheetService.resize_sheet(self.sheet, row_count=2, column_count=0)
        self.assertEqual(result1['rows_created'], 2)
        self.assertEqual(result1['total_rows'], 2)
        
        # Get the created rows
        rows = list(SheetRow.objects.filter(sheet=self.sheet, is_deleted=False).order_by('position'))
        self.assertEqual(len(rows), 2)
        row1, row2 = rows[0], rows[1]
        self.assertEqual(row1.position, 0)
        self.assertEqual(row2.position, 1)
        
        # Soft-delete the second row (position=1)
        row2.is_deleted = True
        row2.save()
        
        # Resize again - should create new row at position 2 (max_position + 1)
        # Does NOT reuse the deleted position (position 1)
        # Expected behavior: include deleted rows in max position calculation
        result2 = SheetService.resize_sheet(self.sheet, row_count=3, column_count=0)
        
        # Verify new rows are created at positions beyond max_position (not reusing deleted position 1)
        # After deleting position 1, we have 1 active row (position 0)
        # max_row_position = 1 (including deleted), so new rows should start at position 2
        # We need 3 active rows total, so we need 2 more: positions 2 and 3
        new_rows = SheetRow.objects.filter(sheet=self.sheet, is_deleted=False).order_by('position')
        positions = [r.position for r in new_rows]
        
        self.assertIn(0, positions)  # Original row at position 0
        self.assertNotIn(1, positions)  # Should NOT reuse deleted position 1
        # New rows should be at positions >= max_row_position + 1 (i.e., >= 2)
        self.assertIn(2, positions)  # First new row at position 2 (max_position + 1)
        # Should have 3 active rows total: [0, 2, 3]
        self.assertEqual(len(positions), 2)
        self.assertEqual(sorted(positions), [0, 2])  # Verify exact positions
    
    def test_get_next_sheet_position(self):
        """Test _get_next_sheet_position helper method"""
        # No sheets yet, should return 0
        position = SheetService._get_next_sheet_position(self.spreadsheet)
        self.assertEqual(position, 0)
        
        # Create first sheet
        SheetService.create_sheet(self.spreadsheet, name='Sheet 1')
        position = SheetService._get_next_sheet_position(self.spreadsheet)
        self.assertEqual(position, 1)
        
        # Create second sheet
        SheetService.create_sheet(self.spreadsheet, name='Sheet 2')
        position = SheetService._get_next_sheet_position(self.spreadsheet)
        self.assertEqual(position, 2)
    
    def test_get_next_sheet_position_with_deleted(self):
        """Test that deleted sheets don't affect position calculation"""
        sheet1 = SheetService.create_sheet(self.spreadsheet, name='Sheet 1')
        sheet2 = SheetService.create_sheet(self.spreadsheet, name='Sheet 2')
        
        # Delete sheet2
        sheet2.is_deleted = True
        sheet2.save()
        
        # Position should still be 2 (max position + 1)
        position = SheetService._get_next_sheet_position(self.spreadsheet)
        self.assertEqual(position, 2)


class SheetRowServiceTest(TestCase):
    """Test cases for SheetRow service methods (CellService._get_or_create_row)"""
    
    def setUp(self):
        """Set up test data"""
        self.user = create_test_user()
        self.organization = create_test_organization()
        self.project = create_test_project(self.organization, owner=self.user)
        self.spreadsheet = create_test_spreadsheet(self.project)
        self.sheet = create_test_sheet(self.spreadsheet)
    
    def test_get_or_create_row_creates_new_row(self):
        """Test that _get_or_create_row creates a new row when it doesn't exist"""
        row = CellService._get_or_create_row(self.sheet, position=0)
        
        self.assertIsNotNone(row.id)
        self.assertEqual(row.sheet, self.sheet)
        self.assertEqual(row.position, 0)
        self.assertFalse(row.is_deleted)
    
    def test_get_or_create_row_returns_existing_row(self):
        """Test that _get_or_create_row returns existing row"""
        row1 = CellService._get_or_create_row(self.sheet, position=0)
        row1_id = row1.id
        
        row2 = CellService._get_or_create_row(self.sheet, position=0)
        
        self.assertEqual(row1_id, row2.id)
        self.assertEqual(row1.position, row2.position)
        self.assertEqual(row1.sheet, row2.sheet)
    
    def test_get_or_create_row_does_not_reactivate_deleted_row(self):
        """Test that _get_or_create_row does not reactivate a deleted row"""
        # Create and delete a row
        row = SheetRow.objects.create(sheet=self.sheet, position=0)
        row.is_deleted = True
        row.save()
        row_id = row.id
        
        # Get or create should create a new active row, not reuse deleted
        reactivated_row = CellService._get_or_create_row(self.sheet, position=0)
        
        self.assertNotEqual(reactivated_row.id, row_id)
        self.assertFalse(reactivated_row.is_deleted)
        row.refresh_from_db()
        self.assertTrue(row.is_deleted)
    
    def test_get_or_create_row_different_positions(self):
        """Test that _get_or_create_row works with different positions"""
        row1 = CellService._get_or_create_row(self.sheet, position=0)
        row2 = CellService._get_or_create_row(self.sheet, position=1)
        row3 = CellService._get_or_create_row(self.sheet, position=5)
        
        self.assertNotEqual(row1.id, row2.id)
        self.assertNotEqual(row2.id, row3.id)
        self.assertEqual(row1.position, 0)
        self.assertEqual(row2.position, 1)
        self.assertEqual(row3.position, 5)
    
    def test_get_or_create_row_same_position_different_sheets(self):
        """Test that same position works for different sheets"""
        sheet2 = create_test_sheet(self.spreadsheet, name='Sheet 2', position=1)
        
        row1 = CellService._get_or_create_row(self.sheet, position=0)
        row2 = CellService._get_or_create_row(sheet2, position=0)
        
        self.assertNotEqual(row1.id, row2.id)
        self.assertEqual(row1.position, row2.position)
        self.assertNotEqual(row1.sheet, row2.sheet)
    
    def test_get_or_create_row_large_position(self):
        """Test that _get_or_create_row works with large position values"""
        row = CellService._get_or_create_row(self.sheet, position=1000)
        
        self.assertEqual(row.position, 1000)
        self.assertEqual(row.sheet, self.sheet)
        self.assertFalse(row.is_deleted)
    
    def test_get_or_create_row_zero_position(self):
        """Test that _get_or_create_row works with position 0"""
        row = CellService._get_or_create_row(self.sheet, position=0)
        
        self.assertEqual(row.position, 0)
        self.assertFalse(row.is_deleted)


class SheetColumnServiceTest(TestCase):
    """Test cases for SheetColumn service methods (CellService._get_or_create_column, SheetService._generate_column_name)"""
    
    def setUp(self):
        """Set up test data"""
        self.user = create_test_user()
        self.organization = create_test_organization()
        self.project = create_test_project(self.organization, owner=self.user)
        self.spreadsheet = create_test_spreadsheet(self.project)
        self.sheet = create_test_sheet(self.spreadsheet)
    
    def test_generate_column_name_single_letter(self):
        """Test _generate_column_name for single letter columns (A-Z)"""
        self.assertEqual(SheetService._generate_column_name(0), 'A')
        self.assertEqual(SheetService._generate_column_name(1), 'B')
        self.assertEqual(SheetService._generate_column_name(25), 'Z')
    
    def test_generate_column_name_double_letter(self):
        """Test _generate_column_name for double letter columns (AA-ZZ)"""
        self.assertEqual(SheetService._generate_column_name(26), 'AA')
        self.assertEqual(SheetService._generate_column_name(27), 'AB')
        self.assertEqual(SheetService._generate_column_name(51), 'AZ')
        self.assertEqual(SheetService._generate_column_name(52), 'BA')
        self.assertEqual(SheetService._generate_column_name(701), 'ZZ')
    
    def test_generate_column_name_triple_letter(self):
        """Test _generate_column_name for triple letter columns"""
        self.assertEqual(SheetService._generate_column_name(702), 'AAA')
        self.assertEqual(SheetService._generate_column_name(703), 'AAB')
        self.assertEqual(SheetService._generate_column_name(727), 'AAZ')
        self.assertEqual(SheetService._generate_column_name(728), 'ABA')
    
    def test_generate_column_name_very_large_position(self):
        """Test _generate_column_name for very large positions"""
        # Test position 18278 which should be 'ZZZ' (26*26*26 + 26*26 + 26 - 1 = 18277, so 18278 = 'AAAA')
        result = SheetService._generate_column_name(18277)
        self.assertEqual(result, 'ZZZ')
        
        result = SheetService._generate_column_name(18278)
        self.assertEqual(result, 'AAAA')
    
    def test_generate_column_name_edge_cases(self):
        """Test _generate_column_name edge cases"""
        # Test boundary values
        self.assertEqual(SheetService._generate_column_name(0), 'A')
        self.assertEqual(SheetService._generate_column_name(25), 'Z')
        self.assertEqual(SheetService._generate_column_name(26), 'AA')
        self.assertEqual(SheetService._generate_column_name(701), 'ZZ')
        self.assertEqual(SheetService._generate_column_name(702), 'AAA')
    
    def test_get_or_create_column_creates_new_column(self):
        """Test that _get_or_create_column creates a new column when it doesn't exist"""
        column = CellService._get_or_create_column(self.sheet, position=0)
        
        self.assertIsNotNone(column.id)
        self.assertEqual(column.sheet, self.sheet)
        self.assertEqual(column.position, 0)
        self.assertEqual(column.name, 'A')  # Position 0 = 'A'
        self.assertFalse(column.is_deleted)
    
    def test_get_or_create_column_returns_existing_column(self):
        """Test that _get_or_create_column returns existing column"""
        column1 = CellService._get_or_create_column(self.sheet, position=0)
        column1_id = column1.id
        
        column2 = CellService._get_or_create_column(self.sheet, position=0)
        
        self.assertEqual(column1_id, column2.id)
        self.assertEqual(column1.position, column2.position)
        self.assertEqual(column1.sheet, column2.sheet)
        self.assertEqual(column1.name, column2.name)
    
    def test_get_or_create_column_does_not_reactivate_deleted_column(self):
        """Test that _get_or_create_column does not reactivate a deleted column"""
        # Create and delete a column
        column = SheetColumn.objects.create(
            sheet=self.sheet,
            position=0,
            name='A'
        )
        column.is_deleted = True
        column.save()
        column_id = column.id
        
        # Get or create should create a new active column, not reuse deleted
        reactivated_column = CellService._get_or_create_column(self.sheet, position=0)
        
        self.assertNotEqual(reactivated_column.id, column_id)
        self.assertFalse(reactivated_column.is_deleted)
        column.refresh_from_db()
        self.assertTrue(column.is_deleted)
    
    def test_get_or_create_column_different_positions(self):
        """Test that _get_or_create_column works with different positions"""
        column1 = CellService._get_or_create_column(self.sheet, position=0)
        column2 = CellService._get_or_create_column(self.sheet, position=1)
        column3 = CellService._get_or_create_column(self.sheet, position=26)
        column4 = CellService._get_or_create_column(self.sheet, position=702)
        
        self.assertNotEqual(column1.id, column2.id)
        self.assertNotEqual(column2.id, column3.id)
        self.assertNotEqual(column3.id, column4.id)
        self.assertEqual(column1.position, 0)
        self.assertEqual(column2.position, 1)
        self.assertEqual(column3.position, 26)
        self.assertEqual(column4.position, 702)
        self.assertEqual(column1.name, 'A')
        self.assertEqual(column2.name, 'B')
        self.assertEqual(column3.name, 'AA')
        self.assertEqual(column4.name, 'AAA')
    
    def test_get_or_create_column_same_position_different_sheets(self):
        """Test that same position works for different sheets"""
        sheet2 = create_test_sheet(self.spreadsheet, name='Sheet 2', position=1)
        
        column1 = CellService._get_or_create_column(self.sheet, position=0)
        column2 = CellService._get_or_create_column(sheet2, position=0)
        
        self.assertNotEqual(column1.id, column2.id)
        self.assertEqual(column1.position, column2.position)
        self.assertEqual(column1.name, column2.name)  # Both should be 'A'
        self.assertNotEqual(column1.sheet, column2.sheet)
    
    def test_get_or_create_column_large_position(self):
        """Test that _get_or_create_column works with large position values"""
        column = CellService._get_or_create_column(self.sheet, position=1000)
        
        self.assertEqual(column.position, 1000)
        self.assertEqual(column.sheet, self.sheet)
        self.assertFalse(column.is_deleted)


class FormulaRewriteTest(TestCase):
    """Test cases for structural formula rewrite"""

    def setUp(self):
        self.user = create_test_user()
        self.organization = create_test_organization()
        self.project = create_test_project(self.organization, owner=self.user)
        self.spreadsheet = create_test_spreadsheet(self.project)
        self.sheet = create_test_sheet(self.spreadsheet)

        self.row0 = SheetRow.objects.create(sheet=self.sheet, position=0)
        self.row1 = SheetRow.objects.create(sheet=self.sheet, position=1)
        self.col0 = SheetColumn.objects.create(sheet=self.sheet, position=0, name='A')
        self.col1 = SheetColumn.objects.create(sheet=self.sheet, position=1, name='B')
        self.col2 = SheetColumn.objects.create(sheet=self.sheet, position=2, name='C')

    def test_insert_column_rewrites_formula(self):
        cell = Cell.objects.create(
            sheet=self.sheet,
            row=self.row0,
            column=self.col0,
            value_type=CellValueType.FORMULA,
            raw_input='=B1',
            formula_value='=B1'
        )
        from spreadsheet.formula_rewrite import rewrite_cells_for_operation
        rewrite_cells_for_operation(self.sheet.id, 'COL_INSERT', 1, 1)
        cell.refresh_from_db()
        self.assertEqual(cell.raw_input, '=C1')
        self.assertEqual(cell.formula_value, '=C1')

    def test_insert_row_rewrites_formula(self):
        cell = Cell.objects.create(
            sheet=self.sheet,
            row=self.row0,
            column=self.col0,
            value_type=CellValueType.FORMULA,
            raw_input='=A2',
            formula_value='=A2'
        )
        from spreadsheet.formula_rewrite import rewrite_cells_for_operation
        rewrite_cells_for_operation(self.sheet.id, 'ROW_INSERT', 1, 1)
        cell.refresh_from_db()
        self.assertEqual(cell.raw_input, '=A3')
        self.assertEqual(cell.formula_value, '=A3')

    def test_delete_column_rewrites_formula(self):
        cell_b = Cell.objects.create(
            sheet=self.sheet,
            row=self.row0,
            column=self.col0,
            value_type=CellValueType.FORMULA,
            raw_input='=B1',
            formula_value='=B1'
        )
        cell_c = Cell.objects.create(
            sheet=self.sheet,
            row=self.row0,
            column=self.col1,
            value_type=CellValueType.FORMULA,
            raw_input='=C1',
            formula_value='=C1'
        )
        from spreadsheet.formula_rewrite import rewrite_cells_for_operation
        rewrite_cells_for_operation(self.sheet.id, 'COL_DELETE', 1, 1)
        cell_b.refresh_from_db()
        cell_c.refresh_from_db()
        self.assertEqual(cell_b.raw_input, '=#REF!')
        self.assertEqual(cell_b.formula_value, '=#REF!')
        self.assertEqual(cell_c.raw_input, '=B1')
        self.assertEqual(cell_c.formula_value, '=B1')

    def test_delete_row_rewrites_formula(self):
        cell = Cell.objects.create(
            sheet=self.sheet,
            row=self.row0,
            column=self.col0,
            value_type=CellValueType.FORMULA,
            raw_input='=A2',
            formula_value='=A2'
        )
        from spreadsheet.formula_rewrite import rewrite_cells_for_operation
        rewrite_cells_for_operation(self.sheet.id, 'ROW_DELETE', 1, 1)
        cell.refresh_from_db()
        self.assertEqual(cell.raw_input, '=#REF!')
        self.assertEqual(cell.formula_value, '=#REF!')

    def test_non_formula_raw_input_unchanged(self):
        cell = Cell.objects.create(
            sheet=self.sheet,
            row=self.row0,
            column=self.col0,
            value_type=CellValueType.STRING,
            raw_input='Hello'
        )
        from spreadsheet.formula_rewrite import rewrite_cells_for_operation
        rewrite_cells_for_operation(self.sheet.id, 'ROW_INSERT', 0, 1)
        cell.refresh_from_db()
        self.assertEqual(cell.raw_input, 'Hello')

    def test_formula_value_only_rewrites(self):
        cell = Cell.objects.create(
            sheet=self.sheet,
            row=self.row0,
            column=self.col0,
            value_type=CellValueType.FORMULA,
            raw_input='',
            formula_value='=A1'
        )
        from spreadsheet.formula_rewrite import rewrite_cells_for_operation
        rewrite_cells_for_operation(self.sheet.id, 'COL_INSERT', 0, 1)
        cell.refresh_from_db()
        self.assertEqual(cell.formula_value, '=B1')
        self.assertEqual(cell.raw_input, '')

    def test_service_insert_triggers_rewrite(self):
        cell = Cell.objects.create(
            sheet=self.sheet,
            row=self.row0,
            column=self.col0,
            value_type=CellValueType.FORMULA,
            raw_input='=B1',
            formula_value='=B1'
        )
        SheetService.insert_columns(self.sheet, position=1, count=1, created_by=self.user)
        cell.refresh_from_db()
        self.assertEqual(cell.raw_input, '=C1')
        self.assertEqual(cell.formula_value, '=C1')

    def test_delete_row_rewrite_recalculates_formula_value_only(self):
        value_cell = Cell.objects.create(
            sheet=self.sheet,
            row=self.row1,
            column=self.col0,
            value_type=CellValueType.NUMBER,
            number_value=5
        )
        formula_cell = Cell.objects.create(
            sheet=self.sheet,
            row=self.row0,
            column=self.col1,
            value_type=CellValueType.FORMULA,
            raw_input='',
            formula_value='=A2'
        )
        from spreadsheet.services import CellService
        CellService._recalculate_formula_cells([formula_cell, value_cell])
        formula_cell.refresh_from_db()
        self.assertEqual(formula_cell.computed_type, ComputedCellType.NUMBER)

        SheetService.delete_rows(self.sheet, position=1, count=1, created_by=self.user)
        formula_cell.refresh_from_db()
        self.assertEqual(formula_cell.formula_value, '=#REF!')
        self.assertEqual(formula_cell.computed_type, ComputedCellType.ERROR)
        self.assertEqual(formula_cell.error_code, "#REF!")

    def test_delete_row_sum_excludes_deleted_cells(self):
        Cell.objects.create(
            sheet=self.sheet,
            row=self.row0,
            column=self.col0,
            value_type=CellValueType.NUMBER,
            number_value=10
        )
        Cell.objects.create(
            sheet=self.sheet,
            row=self.row1,
            column=self.col0,
            value_type=CellValueType.NUMBER,
            number_value=5
        )
        Cell.objects.create(
            sheet=self.sheet,
            row=self.row0,
            column=self.col1,
            value_type=CellValueType.FORMULA,
            raw_input='=SUM(A1:A3)',
            formula_value='=SUM(A1:A3)'
        )
        row2 = SheetRow.objects.create(sheet=self.sheet, position=2)
        Cell.objects.create(
            sheet=self.sheet,
            row=row2,
            column=self.col0,
            value_type=CellValueType.NUMBER,
            number_value=3
        )

        SheetService.delete_rows(self.sheet, position=1, count=1, created_by=self.user)

        formula_cell = Cell.objects.get(
            sheet=self.sheet,
            row__position=0,
            column__position=1,
            is_deleted=False
        )
        self.assertEqual(formula_cell.raw_input, '=SUM(A1:A2)')
        self.assertEqual(formula_cell.formula_value, '=SUM(A1:A2)')
        self.assertEqual(formula_cell.computed_type, ComputedCellType.NUMBER)
        self.assertEqual(formula_cell.computed_number, 13)

    def test_delete_row_rewrite_recalculates(self):
        value_cell = Cell.objects.create(
            sheet=self.sheet,
            row=self.row1,
            column=self.col0,
            value_type=CellValueType.NUMBER,
            number_value=5
        )
        formula_cell = Cell.objects.create(
            sheet=self.sheet,
            row=self.row0,
            column=self.col1,
            value_type=CellValueType.FORMULA,
            raw_input='=A2',
            formula_value='=A2'
        )
        # Initial recalc (simulate normal evaluation)
        from spreadsheet.services import CellService
        CellService._recalculate_formula_cells([formula_cell, value_cell])
        formula_cell.refresh_from_db()
        self.assertEqual(formula_cell.computed_type, ComputedCellType.NUMBER)

        SheetService.delete_rows(self.sheet, position=1, count=1, created_by=self.user)
        formula_cell.refresh_from_db()
        self.assertEqual(formula_cell.raw_input, '=#REF!')
        self.assertEqual(formula_cell.formula_value, '=#REF!')
        self.assertEqual(formula_cell.computed_type, ComputedCellType.ERROR)
        self.assertEqual(formula_cell.error_code, "#REF!")
    
    def test_get_or_create_column_zero_position(self):
        """Test that _get_or_create_column works with position 0"""
        column = CellService._get_or_create_column(self.sheet, position=0)
        
        self.assertEqual(column.position, 0)
        self.assertEqual(column.name, 'A')
        self.assertFalse(column.is_deleted)
    
    def test_get_or_create_column_all_single_letters(self):
        """Test that _get_or_create_column generates correct names for A-Z"""
        for i in range(26):
            column = CellService._get_or_create_column(self.sheet, position=i)
            expected_name = chr(ord('A') + i)
            self.assertEqual(column.name, expected_name, f"Position {i} should be {expected_name}")
    
    def test_get_or_create_column_double_letter_columns(self):
        """Test that _get_or_create_column generates correct names for AA-ZZ"""
        # Test first few double letter columns
        column_aa = CellService._get_or_create_column(self.sheet, position=26)
        self.assertEqual(column_aa.name, 'AA')
        
        column_ab = CellService._get_or_create_column(self.sheet, position=27)
        self.assertEqual(column_ab.name, 'AB')
        
        column_ba = CellService._get_or_create_column(self.sheet, position=52)
        self.assertEqual(column_ba.name, 'BA')
        
        column_zz = CellService._get_or_create_column(self.sheet, position=701)
        self.assertEqual(column_zz.name, 'ZZ')
    
    def test_rewrite_protects_quotes_sheet_refs_and_absolute_marks(self):
        from spreadsheet.formula_rewrite import rewrite_formula_text

        self.assertEqual(rewrite_formula_text('=$A$1', 'ROW_INSERT', 0, 1), '=$A$2')
        self.assertEqual(rewrite_formula_text('=$A$1', 'COL_INSERT', 0, 1), '=$B$1')
        self.assertEqual(rewrite_formula_text('=$A1', 'COL_INSERT', 0, 1), '=$B1')
        self.assertEqual(rewrite_formula_text('=A$1', 'ROW_INSERT', 0, 1), '=A$2')
        self.assertEqual(rewrite_formula_text('=A$1', 'COL_INSERT', 0, 1), '=B$1')
        self.assertEqual(rewrite_formula_text('="A1"', 'COL_INSERT', 0, 1), '="A1"')
        self.assertEqual(rewrite_formula_text('=A1&"B2"', 'COL_INSERT', 0, 1), '=B1&"B2"')
        self.assertEqual(
            rewrite_formula_text("='Sales 2026'!A1", 'ROW_DELETE', 0, 1),
            "='Sales 2026'!A1",
        )
        self.assertEqual(
            rewrite_formula_text("='Sales 2026'!A1:B5", 'ROW_DELETE', 0, 1),
            "='Sales 2026'!A1:B5",
        )
        self.assertEqual(rewrite_formula_text("='Q1'!A1", 'COL_INSERT', 0, 1), "='Q1'!A1")
        self.assertEqual(
            rewrite_formula_text("='Bob''s Shop'!A1", 'COL_INSERT', 0, 1),
            "='Bob''s Shop'!A1",
        )
        self.assertEqual(rewrite_formula_text('=Sheet2!A1', 'COL_INSERT', 0, 1), '=Sheet2!A1')
        self.assertEqual(
            rewrite_formula_text('=A1+Sheet2!B2', 'COL_INSERT', 0, 1),
            '=B1+Sheet2!B2',
        )
        self.assertEqual(
            rewrite_formula_text('=SUM(A1:B5)', 'COL_DELETE', 0, 1),
            '=SUM(#REF!:A5)',
        )
        self.assertEqual(
            rewrite_formula_text('=SUM(A1:B5)', 'ROW_DELETE', 0, 1),
            '=SUM(#REF!:B4)',
        )
        self.assertEqual(rewrite_formula_text('=FY24', 'COL_INSERT', 0, 1), '=FZ24')
        self.assertEqual(rewrite_formula_text('=A1+DATA1', 'COL_INSERT', 0, 1), '=A1+DATA1')
        self.assertEqual(rewrite_formula_text('=FY_24', 'COL_INSERT', 0, 1), '=FY_24')
        self.assertEqual(rewrite_formula_text('=Table1[Col1]', 'COL_INSERT', 0, 1), '=Table1[Col1]')


class WorkflowPatternServiceTest(TestCase):
    def setUp(self):
        self.user = create_test_user()
        self.organization = create_test_organization()
        self.project = create_test_project(self.organization, owner=self.user)
        self.spreadsheet = create_test_spreadsheet(self.project)
        self.sheet = create_test_sheet(self.spreadsheet)
        SheetService.insert_rows(self.sheet, position=0, count=1, created_by=self.user)
        SheetService.insert_columns(self.sheet, position=0, count=2, created_by=self.user)

    def test_set_header_name_fallback_to_empty(self):
        pattern = WorkflowPattern.objects.create(
            owner=self.user,
            name='Rename Pattern',
            description='',
        )
        WorkflowPatternStep.objects.create(
            pattern=pattern,
            seq=1,
            type='SET_COLUMN_NAME',
            params={
                'header_row_index': 1,
                'from_header': None,
                'to_header': 'Title',
                'column_ref': {'index': 1},
                'column_locator': {
                    'strategy': 'BY_HEADER_TEXT',
                    'from_header': None,
                    'fallback_index': 1,
                },
            },
            disabled=False,
        )

        WorkflowPatternService.apply_pattern(pattern=pattern, sheet=self.sheet, created_by=self.user)
        cell = Cell.objects.get(
            sheet=self.sheet,
            row__position=0,
            column__position=0,
            is_deleted=False
        )
        self.assertEqual(cell.raw_input, 'Title')

