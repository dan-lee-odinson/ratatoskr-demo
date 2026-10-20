"""C9 guard in isolation: found, not found, and the boundary at n words."""

from ratatoskr_demo.guard import any_overlap, normalise, overlap

TRANSCRIPT = "Speaker 1: And send me that paper, I'll pass it to our lead. Speaker 2: Done, by Friday."


def test_an_eight_word_span_copied_verbatim_is_found():
    assert overlap(TRANSCRIPT, "Task: send me that paper ill pass it to our lead", 8)


def test_a_paraphrase_is_not_found():
    assert not overlap(TRANSCRIPT, "Email the preprint to the thermal lead by Friday", 8)


def test_seven_matching_words_are_allowed_when_n_is_eight():
    assert not overlap(TRANSCRIPT, "send me that paper I'll pass it", 8)


def test_exactly_n_matching_words_are_caught():
    assert overlap(TRANSCRIPT, "send me that paper I'll pass it to", 8)


def test_case_and_punctuation_do_not_hide_a_copy():
    assert overlap(TRANSCRIPT, "SEND me -- that PAPER, I'll pass... it TO our lead!", 8)


def test_apostrophes_are_dropped_and_other_punctuation_becomes_space():
    assert normalise("You're at aldrin-micro.EXAMPLE") == "youre at aldrin micro example"


def test_output_shorter_than_n_words_never_matches():
    assert not overlap(TRANSCRIPT, "send me", 8)


def test_the_span_length_is_configurable():
    assert overlap(TRANSCRIPT, "send me that paper", 4)
    assert not overlap(TRANSCRIPT, "send me that paper", 5)


def test_any_overlap_checks_each_output_separately():
    halves = ["send me that paper", "I'll pass it to our lead"]
    assert not any_overlap(TRANSCRIPT, halves, 8)
    assert any_overlap(TRANSCRIPT, halves + [" ".join(halves)], 8)
