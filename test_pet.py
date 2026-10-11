"""Regression tests use a temporary plan and never alter Windows startup."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt, QObject, Signal
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
import companion
import main
import configure_api
from supervision import ANGER_THRESHOLD, CALM_DELAY, INTERVAL, RESPONSE_WINDOW, Supervisor
from windows_session import WindowsSession


class FakeSession(QObject):
    changed = Signal(bool)

    def __init__(self, *args):
        super().__init__()
        self.available = True

    def refresh(self):
        pass

    def close(self):
        pass

    def set_available(self, available):
        self.available = available
        self.changed.emit(available)

APP = QApplication.instance() or QApplication([])
APP.setFont(QFont("Microsoft YaHei UI",10))

class PetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.plan = self.root / "plan.md"
        self.plan.write_bytes(main.PLAN_PATH.read_bytes())
        self.config_patch = patch.object(main,"CONFIG_PATH", self.root / "config.json")
        self.config_patch.start()
        self.registry = patch.object(main,"startup_enabled", return_value=False)
        self.registry.start()
        self.session_patch = patch.object(main,"WindowsSession",FakeSession)
        self.session_patch.start()
        self.controllers=[]

    def controller(self):
        main.save_config({"startup_enabled":False})
        c=main.DesktopPet()
        self.controllers.append(c)
        c.start()
        c.startup_timer.stop()
        c.tick_timer.stop()
        return c

    def tearDown(self):
        for c in self.controllers:
            c.stop_workers()
        for widget in APP.topLevelWidgets():
            widget.close()
        self.session_patch.stop()
        self.registry.stop()
        self.config_patch.stop()
        self.tmp.cleanup()

    def test_only_selected_p0_status_changes_and_backup(self):
        original = self.plan.read_bytes()
        rows = main.read_tasks(self.plan)
        self.assertGreater(len(rows),0)
        main.complete_tasks({rows[0].number},self.plan)
        after = self.plan.read_bytes()
        changed = [i for i,(a,b) in enumerate(zip(original.splitlines(),after.splitlines())) if a!=b]
        self.assertEqual(changed,[rows[0].line_index])
        self.assertEqual(self.plan.with_suffix(".md.bak").read_bytes(),original)
        self.assertTrue(main.read_tasks(self.plan)[0].status.startswith("已完成"))
        main.complete_tasks({rows[0].number},self.plan)
        self.assertEqual(self.plan.read_bytes(),after)

    def test_crlf_bom_preserved(self):
        self.plan.write_bytes(b"\xef\xbb\xbf"+self.plan.read_text(encoding="utf-8").replace("\n","\r\n").encode())
        main.complete_tasks({"1"},self.plan)
        raw = self.plan.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\n",raw.replace(b"\r\n",b""))

    def test_paused_startup_and_stale_api(self):
        main.save_config({"reminder_enabled":False,"startup_enabled":False})
        c = main.DesktopPet()
        self.controllers.append(c)
        c.start()
        self.assertFalse(c.startup_timer.isActive())
        self.assertFalse(c.supervisor.active)
        self.assertTrue(c.pause_action.isChecked())
        c.welcome()
        self.assertFalse(c.bubble.isVisible())
        rid = c.request_id
        c.toggle_pause(True)
        c._show_api_result("stale",rid)
        self.assertFalse(c.bubble.isVisible())
        c.stop_workers()

    def test_changes_are_not_completion(self):
        c = self.controller()
        rows = main.read_tasks()
        c.supervisor.no_progress=3700
        c.supervisor.ignored=2
        c.supervisor.angry=True
        renamed=[main.Task(t.number,t.title+"renamed",t.status,t.line_index) for t in rows]
        with patch.object(main,"read_tasks",return_value=renamed):
            self.assertFalse(c.sync_progress())
        with patch.object(main,"read_tasks",return_value=renamed[1:]):
            self.assertFalse(c.sync_progress())
        self.assertEqual(c.supervisor.no_progress,3700)
        self.assertTrue(c.supervisor.angry)
        finished=[main.Task(t.number,t.title,"已完成",t.line_index) for t in renamed[1:]]
        with patch.object(main,"read_tasks",return_value=finished):
            self.assertTrue(c.sync_progress())
        self.assertEqual(c.supervisor.no_progress,0)
        self.assertFalse(c.supervisor.active)
        self.assertEqual(c.pet.state,"normal")

    def test_config_and_local_api_fallback(self):
        main.save_config({"reminder_interval_minutes":"broken","startup_reminder_seconds":-2})
        config = main.load_config()
        self.assertEqual(config["reminder_interval_minutes"],30)
        self.assertEqual(config["startup_reminder_seconds"],1)
        self.assertIsNone(main.api_message([],config))
        with patch("urllib.request.urlopen",side_effect=TimeoutError):
            self.assertIsNone(main.api_message([],{"api_key":"test","api_enabled":True}))

    def test_frames_play_and_click_does_not_calm_anger(self):
        widget = main.PetWidget()
        widget.show()
        hits=[]
        widget.clicked.connect(lambda:hits.append(True))
        QTest.mouseClick(widget,Qt.MouseButton.LeftButton,pos=QPoint(90,100))
        self.assertEqual(len(hits),1)
        self.assertEqual(widget.state,"normal")
        widget.timer.stop()
        widget.frame=0
        first=widget.grab().toImage()
        widget.frame=2
        self.assertNotEqual(first,widget.grab().toImage())
        widget.frame=4
        widget.advance()
        self.assertEqual(widget.timer.interval(),3180)
        widget.set_state("angry")
        QTest.mouseClick(widget,Qt.MouseButton.LeftButton,pos=QPoint(90,100))
        self.assertEqual(widget.state,"angry")
        widget.frame=4
        widget.advance()
        self.assertEqual(widget.timer.interval(),780)
        widget.timer.stop()

    def test_api_shutdown_waits_for_worker(self):
        c=main.DesktopPet()
        self.controllers.append(c)
        with patch.object(main,"api_message",side_effect=lambda *args: (time.sleep(0.1) or "ok")):
            worker=main.ApiWorker([],{},False)
            c.api_threads.add(worker)
            worker.start()
            c.stop_workers()
            self.assertFalse(worker.isRunning())

    def test_dialog_saves_checked_task_only(self):
        read, complete = main.read_tasks, main.complete_tasks
        main.save_config({"startup_enabled":False})
        with patch.object(main,"read_tasks",side_effect=lambda:read(self.plan)), \
             patch.object(main,"complete_tasks",side_effect=lambda selected:complete(selected,self.plan)):
            c=main.DesktopPet()
            self.controllers.append(c)
            c.open_tasks()
            c.dialog.list.item(0).setCheckState(Qt.CheckState.Checked)
            button=next(b for b in c.dialog.findChildren(QPushButton) if b.text()=="保存")
            QTest.mouseClick(button,Qt.MouseButton.LeftButton)
            updated=read(self.plan)
            self.assertTrue(updated[0].status.startswith("已完成"))
            self.assertTrue(all(not t.status.startswith("已完成") for t in updated[1:]))
            c.stop_workers()

    def test_api_configuration_preserves_user_preferences(self):
        target=self.root / "api_config.json"
        target.write_text(json.dumps({"reminder_enabled":False,"startup_enabled":False}),encoding="utf-8")
        with patch.object(configure_api,"TARGET",target), \
             patch.object(configure_api.getpass,"getpass",return_value="test-key"):
            configure_api.main()
        config=json.loads(target.read_text(encoding="utf-8"))
        self.assertFalse(config["reminder_enabled"])
        self.assertFalse(config["startup_enabled"])
        self.assertTrue(config["api_enabled"])

    def make_angry(self,c):
        for seconds in (1800,60,1740,60):
            c.advance_supervision(seconds)
        self.assertTrue(c.supervisor.angry)
        self.assertEqual(c.pet.state,"angry")

    def test_one_ignored_reminder_turns_on_angry_animation(self):
        c=self.controller()
        c.supervisor.no_progress=ANGER_THRESHOLD+60
        c.advance_supervision(1800)
        self.assertEqual(c.pet.state,"normal")
        self.assertFalse(c.supervisor.angry)
        c.advance_supervision(RESPONSE_WINDOW)
        self.assertTrue(c.supervisor.angry)
        self.assertEqual(c.pet.state,"angry")
        text=main.local_message([main.Task("1","写作业","未开始",0)],{},True)
        self.assertIn("还不理我",text)

    def test_chat_switch_sends_message_without_touching_supervision(self):
        c=self.controller()
        c.toggle_chat(True)
        self.assertTrue(c.chat_action.isChecked())
        before=(c.supervisor.ignored,c.supervisor.angry,c.supervisor.response_left,c.supervisor.next_reminder)
        c.chat_now()
        self.assertTrue(c.bubble.isVisible())
        self.assertEqual((c.supervisor.ignored,c.supervisor.angry,c.supervisor.response_left,c.supervisor.next_reminder),before)
        self.assertAlmostEqual(c.chat.remaining,companion.CHAT_INTERVAL)

    def test_chat_resets_only_on_hard_gates(self):
        c=self.controller()
        c.toggle_chat(True)
        c.session.set_available(False)
        c.advance_chat(companion.CHAT_INTERVAL)
        self.assertAlmostEqual(c.chat.remaining,companion.CHAT_INTERVAL)
        c.session.set_available(True)
        c.toggle_visible(False)
        c.advance_chat(companion.CHAT_INTERVAL)
        self.assertAlmostEqual(c.chat.remaining,companion.CHAT_INTERVAL)
        c.toggle_visible(True)
        c.toggle_chat(False)
        c.advance_chat(companion.CHAT_INTERVAL)
        self.assertAlmostEqual(c.chat.remaining,companion.CHAT_INTERVAL)

    def test_chat_keeps_deadline_while_bubble_busy_then_speaks(self):
        c=self.controller()
        c.welcome()
        c.toggle_chat(True)
        self.assertTrue(c.bubble.isVisible())
        c.advance_chat(companion.CHAT_INTERVAL)
        # 到点了但气泡被占用：只跳过发送，不重置计时。
        self.assertLessEqual(c.chat.remaining,0)
        c.bubble.hide()
        c.advance_chat(1)
        self.assertTrue(c.bubble.isVisible())

    def test_angry_chat_speaks_in_tease_tone(self):
        c=self.controller()
        c.toggle_chat(True)
        c.supervisor.angry=True
        c.bubble.hide()
        c.send_chat("care")
        context=c.chat_context()
        self.assertIn(c.bubble.label.text(),{t.format(**context) for t in companion.local_chat_pool("tease")})

    def test_angry_bubble_auto_hides(self):
        c=self.controller()
        c.supervisor.no_progress=ANGER_THRESHOLD+60
        c.advance_supervision(1800)
        c.advance_supervision(RESPONSE_WINDOW)
        self.assertTrue(c.supervisor.angry)
        self.assertTrue(c.bubble.isVisible())
        self.assertTrue(c.bubble.hide_timer.isActive())
        self.assertEqual(c.bubble.hide_timer.interval(),main.ANGRY_BUBBLE_MS)

    def test_welcome_does_not_count_as_reminder(self):
        c=self.controller()
        c.welcome()
        self.assertIsNone(c.supervisor.response_left)
        self.assertEqual(c.supervisor.ignored,0)
        self.assertEqual(c.supervisor.next_reminder,1800)

    def test_dismiss_and_open_panel_do_not_acknowledge(self):
        c=self.controller()
        c.advance_supervision(1800)
        c.bubble.dismiss.click()
        c.open_tasks()
        self.assertEqual(c.supervisor.response_left,60)
        c.advance_supervision(60)
        self.assertEqual(c.supervisor.ignored,1)
        c.advance_supervision(1740)
        c.advance_supervision(60)
        self.assertEqual(c.pet.state,"angry")
        rid=c.request_id
        c.advance_supervision(CALM_DELAY-120)
        self.assertEqual(c.request_id,rid)
        self.assertEqual(c.pet.state,"angry")
        c.advance_supervision(120)
        self.assertEqual(c.pet.state,"normal")
        self.assertFalse(c.supervisor.angry)

    def test_pet_returns_to_normal_after_calm_delay(self):
        c=self.controller()
        self.make_angry(c)
        # make_angry 的最后一步已经消耗了 60 秒消气倒计时。
        self.assertEqual(c.supervisor.calm_left,CALM_DELAY-60)
        c.advance_supervision(CALM_DELAY-60-1)
        self.assertEqual(c.pet.state,"angry")
        c.advance_supervision(1)
        self.assertEqual(c.pet.state,"normal")
        self.assertFalse(c.supervisor.angry)
        self.assertEqual(c.supervisor.next_reminder,1800)
        self.assertIn("气消了",c.bubble.label.text())

    def test_anger_recurs_because_progress_gate_never_resets(self):
        c=self.controller()
        self.make_angry(c)
        c.advance_supervision(CALM_DELAY)
        self.assertFalse(c.supervisor.angry)
        # 无进展时长仍然超过门槛：下一次提醒被忽略后立刻又生气。
        c.advance_supervision(1800)
        self.assertEqual(c.supervisor.response_left,RESPONSE_WINDOW)
        c.advance_supervision(RESPONSE_WINDOW)
        self.assertTrue(c.supervisor.angry)

    def test_bubble_working_and_panel_later(self):
        c=self.controller()
        self.make_angry(c)
        old=c.supervisor.no_progress
        QTest.mouseClick(c.bubble.working,Qt.MouseButton.LeftButton)
        self.assertFalse(c.supervisor.angry)
        self.assertEqual(c.pet.state,"normal")
        self.assertEqual(c.supervisor.ignored,0)
        self.assertEqual(c.supervisor.next_reminder,3600)
        self.assertGreaterEqual(c.supervisor.no_progress,old)
        c.open_tasks()
        QTest.mouseClick(c.dialog.later,Qt.MouseButton.LeftButton)
        self.assertEqual(c.supervisor.next_reminder,1800)

    def test_suspend_resume_and_gap_never_count_offline(self):
        c=self.controller()
        self.make_angry(c)
        c.session.set_available(False)
        frozen=c.supervisor.no_progress
        c.advance_supervision(12*3600)
        self.assertEqual(c.supervisor.no_progress,frozen)
        self.assertFalse(c.supervisor.angry)
        self.assertFalse(c.bubble.isVisible())
        c.session.set_available(True)
        self.assertEqual(c.supervisor.next_reminder,1800)
        self.assertTrue(c.supervisor.active)
        c.last_tick=time.monotonic()-36000
        c.tick()
        self.assertEqual(c.supervisor.no_progress,frozen)
        self.assertEqual(c.supervisor.ignored,0)

    def test_hide_freezes_and_restart_preserves_only_elapsed(self):
        c=self.controller()
        self.make_angry(c)
        c.toggle_visible(False)
        frozen=c.supervisor.no_progress
        self.assertFalse(c.supervisor.active)
        self.assertEqual(c.supervisor.ignored,0)
        c.stop_workers()
        other=main.DesktopPet()
        self.controllers.append(other)
        other.start()
        self.assertEqual(other.supervisor.no_progress,frozen)
        self.assertFalse(other.supervisor.angry)
        self.assertEqual(other.supervisor.next_reminder,1800)

    def test_api_cannot_reopen_or_extend_dismissed_reminder(self):
        c=self.controller()
        c.advance_supervision(1800)
        rid=c.request_id
        deadline=c.supervisor.response_left
        c.bubble.hide()
        c._show_api_result("late reply",rid)
        self.assertFalse(c.bubble.isVisible())
        self.assertEqual(c.supervisor.response_left,deadline)

    def test_multiline_reminders_fit_and_stay_on_screen(self):
        bubble=main.SpeechBubble()
        bounds=APP.primaryScreen().availableGeometry()
        for text in (main.local_message(main.read_tasks(),{}), "很长的任务标题"*35, "完成啦"):
            bubble.show_message(text,bounds.bottomRight(),60000,True)
            APP.processEvents()
            self.assertGreaterEqual(bubble.label.height(),bubble.label.heightForWidth(bubble.label.width()))
            label_end=bubble.label.mapTo(bubble,QPoint(0,bubble.label.height())).y()
            controls_top=bubble.controls.mapTo(bubble,QPoint()).y()
            self.assertLessEqual(label_end,controls_top)
            self.assertTrue(bounds.contains(bubble.geometry()))
        bubble.close()

    def test_resume_and_completion_tick_do_not_charge_previous_interval(self):
        c=self.controller()
        c.session.set_available(False)
        before=c.supervisor.no_progress
        c.last_tick=time.monotonic()-1
        with patch.object(c.session,"refresh",side_effect=lambda:c.session.set_available(True)):
            c.tick()
        self.assertEqual(c.supervisor.no_progress,before)
        rows=main.read_tasks()
        completed=[main.Task(t.number,t.title,"已完成" if i==0 else t.status,t.line_index) for i,t in enumerate(rows)]
        c.last_tick=time.monotonic()-1
        c.last_plan_check=0
        with patch.object(main,"read_tasks",return_value=completed):
            c.tick()
        self.assertEqual(c.supervisor.no_progress,0)
        self.assertEqual(c.supervisor.next_reminder,1800)


class CompanionTest(unittest.TestCase):
    def test_every_kind_has_local_copy(self):
        context=companion.build_context(name="Deepseek",done=1,left=2,titles=["甲","乙"],minutes=42,hour=9)
        self.assertEqual(set(companion.CHAT_KINDS),set(companion.KIND_LABELS))
        self.assertEqual(len(companion.CHAT_KINDS),len(set(companion.CHAT_KINDS)))
        for kind in companion.CHAT_KINDS:
            text=companion.local_chat_message(kind,context)
            self.assertTrue(text.strip())
            self.assertIn("Deepseek",text)
        self.assertTrue(companion.local_chat_message("unknown",context))

    def test_context_rounds_minutes_and_reads_period(self):
        self.assertEqual(companion.day_period(3),"凌晨")
        self.assertEqual(companion.day_period(23),"深夜")
        context=companion.build_context(done=2,left=3,minutes=91.7,hour=15)
        self.assertEqual((context["total"],context["minutes"],context["period"]),(5,91,"下午"))

    def test_api_prompt_is_not_a_task_nudge(self):
        context=companion.build_context(done=1,left=2,titles=["任务A"],minutes=30,hour=20)
        prompt=companion.api_chat_prompt("daily",context)
        self.assertIn("不是任务提醒",prompt)
        self.assertIn("任务A",prompt)
        self.assertIn("共 3 项",prompt)

    def test_api_returns_none_without_key_or_network(self):
        self.assertIsNone(companion.api_chat_message("daily",companion.build_context(),{}))
        with patch("urllib.request.urlopen",side_effect=TimeoutError):
            self.assertIsNone(companion.api_chat_message("daily",companion.build_context(),{"api_key":"test","api_enabled":True}))

    def test_scheduler_rotates_kinds_and_keeps_deadline(self):
        s=companion.ChatScheduler()
        self.assertFalse(s.advance(companion.CHAT_INTERVAL-1))
        self.assertTrue(s.advance(1))
        self.assertEqual(s.kind(),companion.CHAT_KINDS[0])
        s.consume()
        self.assertEqual(s.kind(),companion.CHAT_KINDS[1])
        self.assertAlmostEqual(s.remaining,companion.CHAT_INTERVAL)
        s.reset()
        self.assertAlmostEqual(s.remaining,companion.CHAT_INTERVAL)
        self.assertEqual(s.kind(),companion.CHAT_KINDS[1])

    def test_every_template_formats(self):
        context=companion.build_context(name="Deepseek",done=1,left=2,titles=["甲"],minutes=42,hour=9)
        for kind in companion.CHAT_KINDS:
            for template in companion.local_chat_pool(kind):
                text=template.format(**context)
                self.assertTrue(text.strip())
                self.assertNotIn("{",text)
        for template in companion.local_chat_pool("care",{"period":"深夜"}):
            self.assertTrue(template.format(**context).strip())

    def test_pools_are_deep_enough(self):
        for kind in companion.CHAT_KINDS:
            self.assertGreaterEqual(len(companion.local_chat_pool(kind)),8,kind)
        self.assertGreaterEqual(len(companion.local_chat_pool("care",{"period":"深夜"})),4)

    def test_night_care_talks_about_sleep(self):
        day=companion.local_chat_pool("care",{"period":"下午"})
        night=companion.local_chat_pool("care",{"period":"深夜"})
        self.assertIsNot(day,night)
        self.assertIn("睡"," ".join(night))
        self.assertNotIn("睡"," ".join(day))
        night_context=companion.build_context(name="Deepseek",minutes=30,hour=2)
        self.assertEqual(night_context["period"],"凌晨")
        self.assertIn(companion.local_chat_message("care",night_context),
                      {t.format(**night_context) for t in night})
        day_context=companion.build_context(name="Deepseek",minutes=30,hour=15)
        self.assertIn(companion.local_chat_message("care",day_context),
                      {t.format(**day_context) for t in day})


class RulesTest(unittest.TestCase):
    def test_full_timeline(self):
        s=Supervisor(active=True)
        self.assertEqual(s.advance(1799),[])
        self.assertEqual(s.advance(1),["reminder"])
        self.assertEqual(s.advance(60),["ignored"])
        self.assertFalse(s.angry)
        # 只忽略一次就够：无进展满 60 分钟时直接生气，第二次提醒不再发出。
        self.assertEqual(s.advance(1740),["angry"])
        self.assertTrue(s.angry)
        # 生气 30 分钟后自动消气，回到 30 分钟提醒周期。
        self.assertEqual(s.advance(CALM_DELAY-1),[])
        self.assertEqual(s.advance(1),["calm"])
        self.assertFalse(s.angry)
        self.assertEqual(s.next_reminder,INTERVAL)

    def test_both_conditions_required(self):
        s=Supervisor(no_progress=3700,active=True)
        self.assertEqual(s.advance(1),[])
        s=Supervisor(no_progress=100,ignored=2,active=True)
        self.assertEqual(s.advance(1),[])
        self.assertFalse(s.angry)

    def test_working_grace_and_one_fresh_ignore(self):
        s=Supervisor(no_progress=3660,ignored=2,angry=True,active=True)
        s.respond(60)
        self.assertEqual(s.advance(3599),[])
        self.assertEqual(s.advance(1),["reminder"])
        # 无进展门槛早已满足，所以这一次忽略立刻触发生气。
        self.assertEqual(s.advance(60),["ignored","angry"])
        self.assertTrue(s.angry)
        s.complete()
        self.assertEqual((s.no_progress,s.ignored,s.angry),(0,0,False))

    def test_single_ignore_is_enough_once_time_condition_met(self):
        s=Supervisor(no_progress=ANGER_THRESHOLD,active=True)
        s.response_left=RESPONSE_WINDOW
        self.assertEqual(s.advance(RESPONSE_WINDOW),["ignored","angry"])

    def test_windows_lock_and_power_notification_mapping(self):
        class Probe:
            available=True
            suspended=False
            def set_available(self,value): self.available=value
            def refresh(self): self.available=not self.suspended
        p=Probe()
        WindowsSession.handle_message(p,0x2B1,7)
        self.assertFalse(p.available)
        WindowsSession.handle_message(p,0x2B1,8)
        self.assertTrue(p.available)
        WindowsSession.handle_message(p,0x218,4)
        self.assertTrue(p.suspended)
        self.assertFalse(p.available)
        WindowsSession.handle_message(p,0x218,18)
        self.assertFalse(p.suspended)
        self.assertTrue(p.available)

if __name__ == "__main__":
    unittest.main(verbosity=2)
