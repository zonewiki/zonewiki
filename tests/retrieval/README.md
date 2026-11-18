# The retrieval floor

A sample wiki, questions asked of it, and the least search must find.
It is what keeps a change to chunking, the tokens, the ranking or the
embedding model from losing answers unseen (Measuring retrieval in
`docs/spec/search.md`).

- `wiki/`: 54 pages and 12 tasks of a made-up household and the bike
  workshop in its yard: projects, recipes, how-tos, meeting notes with the
  same headings (Decisions, Next steps), tickets with ids, people, and a
  third of it in German. Nobody's real notes.
- `questions.json`: 68 questions with the pages, sections or tasks that
  answer them (`zonewiki eval`'s format): in other words than the page's,
  by id and name, in German and English, and across the two.
- `floors.json`: per ranking (`keyword`, `hybrid`) how many questions must
  be answered first, in the first three and in the first eight, and the
  least MRR.

## Running it

- `./run.sh test` holds the keyword ranking to its floor
  (`TestRetrievalHoldsItsFloor`, a Go test: no model needed).
- `./run.sh retrieval` measures both rankings in the app's image, with the
  model: CI's `retrieval` job.

## When it fails

The report lists every question that isn't answered first, with its rank
per ranking. Compare with `main` to see which ones a change lost.

- A change that lost answers: fix the change.
- A change that found more: raise the numbers in `floors.json` to what the
  report says, in the same commit, so the gain is kept. The keyword test
  fails until you do.
- A change that trades answers on purpose (some lost, more found): lower
  and raise the floor in the same commit, and say in its message which
  questions were lost and why that is right.

## Changing the wiki or the questions

Add pages that make search harder (a page that shares words with another's
answer), and questions in the words a person would use, not the page's.
Then run `./run.sh retrieval` and set `floors.json` to what it reports.
Don't tune a page to make a question pass: the floor says what search
does, not what it should.
