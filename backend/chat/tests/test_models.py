import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from chat.models import Chat, ChatParticipant, Message, MessageStatus, ChatType
from core.models import Organization, Team, TeamMember, Project, ProjectMember
pytestmark = pytest.mark.django_db
User = get_user_model()

class TestChatModel:
    """Test cases for Chat model creation and methods"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.organization = Organization.objects.create(name='Test Org')
        self.team1 = Team.objects.create(organization=self.organization, name='Team 1')
        self.team2 = Team.objects.create(organization=self.organization, name='Team 2')
        self.project = Project.objects.create(name='Test Project', organization=self.organization)
        self.user1 = User.objects.create_user(email='user1@test.com', username='user1', password='testpass123')
        self.user2 = User.objects.create_user(email='user2@test.com', username='user2', password='testpass123')
        self.user3 = User.objects.create_user(email='user3@test.com', username='user3', password='testpass123')
        TeamMember.objects.create(user=self.user1, team=self.team1)
        TeamMember.objects.create(user=self.user2, team=self.team1)
        TeamMember.objects.create(user=self.user3, team=self.team2)
        ProjectMember.objects.create(user=self.user1, project=self.project, is_active=True)
        ProjectMember.objects.create(user=self.user2, project=self.project, is_active=True)

    def test_private_chat_creation(self):
        """Test creating a private chat"""
        chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        assert chat.project == self.project
        assert chat.type == ChatType.PRIVATE
        assert chat.name is None
        assert chat.created_at is not None
        assert chat.updated_at is not None
        assert not chat.is_deleted

    def test_group_chat_creation(self):
        """Test creating a group chat with name"""
        chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Test Group Chat')
        assert chat.type == ChatType.GROUP
        assert chat.name == 'Test Group Chat'

    def test_chat_string_representation(self):
        """Test chat string representation"""
        private_chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        assert 'Private Chat' in str(private_chat)
        group_chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='My Group')
        assert 'Group: My Group' in str(group_chat)

    def test_can_users_chat_same_team(self):
        """Test that users in the same team can chat"""
        can_chat, reason = Chat.can_users_chat(self.user1, self.user2)
        assert can_chat
        assert reason == 'same_team'

    def test_can_users_chat_same_project(self):
        """Test that users in the same project can chat"""
        can_chat, reason = Chat.can_users_chat(self.user1, self.user2)
        assert can_chat

    def test_cannot_chat_no_common_team_or_project(self):
        """Test that users with no common team or project cannot chat"""
        can_chat, reason = Chat.can_users_chat(self.user1, self.user3)
        assert not can_chat
        assert reason == 'no_common_team_or_project'

    def test_get_participant_users(self):
        """Test getting participant users from a chat"""
        chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=chat, user=self.user1)
        ChatParticipant.objects.create(chat=chat, user=self.user2)
        participants = chat.get_participant_users()
        assert len(participants) == 2
        assert self.user1 in participants
        assert self.user2 in participants

    def test_is_user_participant(self):
        """Test checking if user is a participant"""
        chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=chat, user=self.user1)
        assert chat.is_user_participant(self.user1)
        assert not chat.is_user_participant(self.user2)

class TestChatParticipantModel:
    """Test cases for ChatParticipant model"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.organization = Organization.objects.create(name='Test Org')
        self.project = Project.objects.create(name='Test Project', organization=self.organization)
        self.user = User.objects.create_user(email='test@test.com', username='testuser', password='testpass123')
        self.chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)

    def test_participant_creation(self):
        """Test creating a chat participant"""
        participant = ChatParticipant.objects.create(chat=self.chat, user=self.user)
        assert participant.chat == self.chat
        assert participant.user == self.user
        assert participant.is_active
        assert participant.joined_at is not None
        assert participant.last_read_at is None

    def test_participant_unique_constraint(self):
        """Test that a user can only participate once per chat"""
        ChatParticipant.objects.create(chat=self.chat, user=self.user)
        with pytest.raises(Exception):
            ChatParticipant.objects.create(chat=self.chat, user=self.user)

    def test_get_unread_count_never_read(self):
        """Test unread count when user has never read messages"""
        participant = ChatParticipant.objects.create(chat=self.chat, user=self.user)
        other_user = User.objects.create_user(email='other@test.com', username='otheruser', password='testpass123')
        for i in range(3):
            Message.objects.create(chat=self.chat, sender=other_user, content=f'Message {i}')
        assert participant.get_unread_count() == 3

    def test_get_unread_count_with_last_read(self):
        """Test unread count with last_read_at set"""
        participant = ChatParticipant.objects.create(chat=self.chat, user=self.user)
        other_user = User.objects.create_user(email='other@test.com', username='otheruser', password='testpass123')
        old_message = Message.objects.create(chat=self.chat, sender=other_user, content='Old message')
        participant.last_read_at = timezone.now()
        participant.save()
        Message.objects.create(chat=self.chat, sender=other_user, content='New message')
        assert participant.get_unread_count() == 1

class TestMessageModel:
    """Test cases for Message model"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.organization = Organization.objects.create(name='Test Org')
        self.project = Project.objects.create(name='Test Project', organization=self.organization)
        self.user = User.objects.create_user(email='test@test.com', username='testuser', password='testpass123')
        self.chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)

    def test_message_creation(self):
        """Test creating a message"""
        message = Message.objects.create(chat=self.chat, sender=self.user, content='Hello, World!')
        assert message.chat == self.chat
        assert message.sender == self.user
        assert message.content == 'Hello, World!'
        assert message.created_at is not None
        assert message.seq == 1
        assert not message.is_deleted

    def test_message_sequence_increments_per_chat(self):
        other_chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Other')

        first = Message.objects.create(chat=self.chat, sender=self.user, content='First')
        second = Message.objects.create(chat=self.chat, sender=self.user, content='Second')
        other_first = Message.objects.create(chat=other_chat, sender=self.user, content='Other first')

        assert (first.seq, second.seq) == (1, 2)
        assert other_first.seq == 1

    def test_message_string_representation(self):
        """Test message string representation"""
        message = Message.objects.create(chat=self.chat, sender=self.user, content='Test message')
        assert self.user.email in str(message)
        assert 'Test message' in str(message)

    def test_message_long_content_preview(self):
        """Test that long messages are truncated in string representation"""
        long_content = 'A' * 100
        message = Message.objects.create(chat=self.chat, sender=self.user, content=long_content)
        assert '...' in str(message)

class TestMessageStatusModel:
    """Test cases for MessageStatus model"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.organization = Organization.objects.create(name='Test Org')
        self.project = Project.objects.create(name='Test Project', organization=self.organization)
        self.sender = User.objects.create_user(email='sender@test.com', username='sender', password='testpass123')
        self.recipient = User.objects.create_user(email='recipient@test.com', username='recipient', password='testpass123')
        self.chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        self.message = Message.objects.create(chat=self.chat, sender=self.sender, content='Test message')

    def test_message_status_creation(self):
        """Test creating a message status"""
        status = MessageStatus.objects.create(message=self.message, user=self.recipient)
        assert status.message == self.message
        assert status.user == self.recipient
        assert status.status == 'sent'
        assert status.delivered_at is None
        assert status.read_at is None

    def test_message_status_unique_constraint(self):
        """Test that each user can only have one status per message"""
        MessageStatus.objects.create(message=self.message, user=self.recipient)
        with pytest.raises(Exception):
            MessageStatus.objects.create(message=self.message, user=self.recipient)

    def test_mark_as_delivered(self):
        """Test marking message as delivered"""
        status = MessageStatus.objects.create(message=self.message, user=self.recipient, status='sent')
        status.mark_as_delivered()
        status.refresh_from_db()
        assert status.status == 'delivered'
        assert status.delivered_at is not None
        assert status.read_at is None

    def test_mark_as_read(self):
        """Test marking message as read"""
        status = MessageStatus.objects.create(message=self.message, user=self.recipient, status='sent')
        status.mark_as_read()
        status.refresh_from_db()
        assert status.status == 'read'
        assert status.delivered_at is not None
        assert status.read_at is not None

    def test_mark_as_read_sets_delivered_at(self):
        """Test that marking as read also sets delivered_at if not set"""
        status = MessageStatus.objects.create(message=self.message, user=self.recipient, status='sent')
        status.mark_as_read()
        status.refresh_from_db()
        assert status.delivered_at is not None
        assert status.read_at is not None
        assert status.delivered_at == status.read_at

    def test_get_status_for_user(self):
        """Test getting status for a specific user"""
        MessageStatus.objects.create(message=self.message, user=self.recipient, status='delivered')
        status = self.message.get_status_for_user(self.recipient)
        assert status == 'delivered'
        other_user = User.objects.create_user(email='other@test.com', username='other', password='testpass123')
        status = self.message.get_status_for_user(other_user)
        assert status is None
